"""Exercise the deployed course API with two disposable users and no email sends.

Credentials are resolved by boto3. Passwords, JWTs and signed URLs stay in memory.
All test-owned users, database rows and S3 objects are removed in finally.
This tests cloud boundaries; queued ingestion is not model acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

import boto3
import requests
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from ecoguard_aws.config import Config as ApplicationConfig
from ecoguard_aws.storage import S3Storage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--account', default='360493183467')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--prefix', default='ecoguard3184-dev')
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--pool-id', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    clients = {name: session.client(name, config=Config(retries={'total_max_attempts': 2}, connect_timeout=5, read_timeout=35)) for name in ('sts', 'cognito-idp', 'lambda')}
    assert clients['sts'].get_caller_identity()['Account'] == args.account, 'Unexpected account'
    cfg = clients['lambda'].get_function_configuration(FunctionName=args.prefix+'-api')['Environment']['Variables']
    assert cfg['ECO_COGNITO_USER_POOL_ID'] == args.pool_id
    checks, observations, created, owners, media_ids, operation_ids = {}, {}, [], [], set(), set()
    result = {'kind': 'actual-two-user-cloud-boundaries', 'account': args.account, 'region': args.region, 'emailsSent': 0, 'checks': checks, 'observations': observations}
    ddb = session.resource('dynamodb')
    storage = S3Storage(ApplicationConfig(region=args.region, media_bucket=cfg['ECO_MEDIA_BUCKET']), session)
    objects = set()
    http = requests.Session()

    def call(method, path, token=None, body=None, headers=None):
        auth = {'Authorization': 'Bearer '+token} if token else {}
        response = http.request(method, args.api_url.rstrip('/')+'/api/v1/'+path, json=body, headers={**auth, **(headers or {})}, timeout=(5, 35))
        try:
            payload = response.json()
        except requests.exceptions.JSONDecodeError:
            payload = {}
        return response.status_code, payload

    def user():
        email = 'ecoguard-acceptance-'+secrets.token_hex(10)+'@example.invalid'
        password = 'Aa!7'+secrets.token_hex(24)
        clients['cognito-idp'].admin_create_user(UserPoolId=args.pool_id, Username=email, MessageAction='SUPPRESS', TemporaryPassword=password, UserAttributes=[{'Name':'email','Value':email},{'Name':'given_name','Value':'Acceptance'},{'Name':'family_name','Value':'Test'}])
        created.append(email)
        clients['cognito-idp'].admin_set_user_password(UserPoolId=args.pool_id, Username=email, Password=password, Permanent=True)
        response = clients['cognito-idp'].initiate_auth(ClientId=args.client_id, AuthFlow='USER_PASSWORD_AUTH', AuthParameters={'USERNAME':email,'PASSWORD':password})['AuthenticationResult']
        details = clients['cognito-idp'].get_user(AccessToken=response['AccessToken'])
        owners.append(next(a['Value'] for a in details['UserAttributes'] if a['Name']=='sub'))
        return response

    def error_code(payload):
        return payload.get('error', {}).get('code')

    try:
        a, b = user(), user()
        token, other = a['AccessToken'], b['AccessToken']
        checks['anonymous401'] = call('GET','media')[0] == 401
        checks['invalidToken401'] = call('GET','media','invalid')[0] == 401
        checks['idToken401'] = call('GET','media',a['IdToken'])[0] == 401
        checks['accessToken200'] = call('GET','media',token)[0] == 200
        response = http.options(args.api_url.rstrip('/')+'/api/v1/media',headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type,idempotency-key'},timeout=30)
        allowed_headers = {value.strip().lower() for value in response.headers.get('access-control-allow-headers','').split(',')}
        checks['corsAllowed'] = response.status_code in (200,204) and response.headers.get('access-control-allow-origin') == 'http://127.0.0.1:5173' and {'authorization','content-type','idempotency-key'} <= allowed_headers
        response = http.options(args.api_url.rstrip('/')+'/api/v1/media',headers={'Origin':'https://untrusted.example.invalid','Access-Control-Request-Method':'POST'},timeout=30)
        checks['corsUntrustedNotAllowed'] = not response.headers.get('access-control-allow-origin')
        data = args.sample.read_bytes()
        meta = {'filename':'acceptance-cat.png','contentType':'image/png','sizeBytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'visibility':'private'}
        status, payload = call('POST','uploads',token,{**meta,'contentType':'text/html'})
        checks['unsupportedMediaRejected'] = status == 422 and error_code(payload) == 'UNSUPPORTED_TYPE'
        status, payload = call('POST','uploads',token,meta)
        checks['uploadReservation201'] = status == 201
        upload = payload['data']; media_id, job = upload['mediaId'], upload['jobId']
        app_media_url = cfg['ECO_WEB_URL'].rstrip('/')+'/media/'+media_id
        media_ids.add(media_id); operation_ids.add(job)
        key = 'incoming/library/'+job; objects.add(key)
        checks['privateMetadataHidden'] = call('GET','media/'+media_id,other)[0] == 404
        checks['otherUserJobHidden'] = call('GET','jobs/'+job,other)[0] == 404
        checks['otherUserVisibilityDenied'] = call('PATCH','media/'+media_id,other,{'visibility':'public'})[0] == 404
        checks['otherUserDeleteDenied'] = call('POST','media/delete',other,{'urls':[app_media_url]})[0] == 404
        bad_headers = dict(upload['headers']);bad_headers['x-amz-checksum-sha256'] = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
        response = http.put(upload['uploadUrl'],data=data,headers=bad_headers,timeout=35)
        checks['signedChecksumTamperingRejected'] = response.status_code in (400,403)
        response = http.put(upload['uploadUrl'],data=data,headers=upload['headers'],timeout=35)
        checks['signedUploadSucceeded'] = response.status_code == 200
        checks['storedChecksumVerified'] = storage.head(key)['sha256'] == meta['sha256']
        plain_url = 'https://'+cfg['ECO_MEDIA_BUCKET']+'.s3.us-east-1.amazonaws.com/'+key
        checks['unsignedObjectDenied'] = http.get(plain_url,timeout=30).status_code == 403
        checks['duplicateIsOwnerScoped'] = call('POST','uploads',token,meta)[1].get('data',{}).get('duplicate') is True
        status, payload = call('POST','uploads/'+job+'/complete',token,{})
        checks['completionQueued'] = status == 202 and payload.get('data',{}).get('status') == 'QUEUED'
        observations['ingestion'] = 'QUEUED: no deployed ingest image or secondary-cloud inference; not a completed model result'
        status, payload = call('POST','media/tags',token,{'urls':[app_media_url],'tags':['cat'],'operation':1})
        checks['tagsBeforeModelCompletionRejected'] = status == 409 and error_code(payload) == 'NOT_READY'
        checks['unverifiedEmailSubscriptionDenied'] = call('POST','subscriptions',token,{'tags':['cat']})[0] == 403
        status, payload = call('POST','query-jobs',token,{'type':'pet','description':'a cat'})
        observations['petQuery'] = {'httpStatus':status,'errorCode':error_code(payload)}
        checks['missingInferenceFailsExplicitly'] = status == 503 and error_code(payload) == 'INFERENCE_UNAVAILABLE'
        status, payload = call('POST','chat/sessions',token,{})
        chat = payload['data']['sessionId']; operation_ids.add('CHAT#'+chat)
        checks['otherUserChatHidden'] = call('POST','chat/messages',other,{'sessionId':chat,'text':'help'})[0] == 404
        status, payload = call('POST','chat/messages',token,{'sessionId':chat,'text':'show me two cats'})
        observations['chat'] = {'httpStatus':status,'errorCode':error_code(payload)}
        checks['missingLexFailsExplicitly'] = status == 503 and error_code(payload) == 'LEX_UNAVAILABLE'
        status, payload = call('POST','media/delete',token,{'urls':[app_media_url]})
        delete_job = payload['data']['items'][0]['jobId'];operation_ids.add(delete_job)
        checks['ownerDeletionAccepted'] = status == 202
        deletion_event = {'Records':[{'messageId':'acceptance-'+delete_job,'body':json.dumps({'entityId':delete_job,'kind':'DELETE'})}]}
        invocation = clients['lambda'].invoke(FunctionName=args.prefix+'-delete',Payload=json.dumps(deletion_event).encode())
        invoked = json.loads(invocation['Payload'].read())
        checks['realDeletionWorkerSucceeded'] = not invocation.get('FunctionError') and invoked.get('batchItemFailures') == []
        item = ddb.Table(cfg['ECO_MEDIA_TABLE']).get_item(Key={'mediaId':media_id}).get('Item',{})
        checks['deletedRecordRemoved'] = not item and call('GET','media/'+media_id,token)[0] == 404
        checks['objectRemovedByWorker'] = key not in set(storage.keys(key))
        operation = ddb.Table(cfg['ECO_OPERATIONS_TABLE']).get_item(Key={'operationId':job}).get('Item',{})
        checks['deletionClosesIngestion'] = operation.get('status') == 'DELETED' and not operation.get('frames') and not operation.get('objectKeys')
    except Exception as exc:
        result['errorType'] = type(exc).__name__
        result['serviceErrorCode'] = (getattr(exc,'response',None) or {}).get('Error',{}).get('Code')
    finally:
        cleanup_errors = []
        for key in objects:
            try: storage.delete(key)
            except Exception as exc: cleanup_errors.append({'kind':'object','errorType':type(exc).__name__})
        for env, key_names in [('ECO_MEDIA_TABLE',['mediaId']),('ECO_OPERATIONS_TABLE',['operationId']),('ECO_OUTBOX_TABLE',['eventId'])]:
            try:
                table = ddb.Table(cfg[env]); owned = set(owners)
                low = session.client('dynamodb'); decoder = TypeDeserializer()
                for page in low.get_paginator('scan').paginate(TableName=table.name):
                    for raw in page.get('Items',[]):
                        row = {key:decoder.deserialize(value) for key,value in raw.items()}
                        if row.get('ownerSub') in owned or row.get('mediaId') in media_ids or row.get('entityId') in operation_ids or row.get('entityId') in media_ids:
                            table.delete_item(Key={key:row[key] for key in key_names})
            except Exception as exc: cleanup_errors.append({'kind':'database','errorType':type(exc).__name__})
        for email in created:
            try: clients['cognito-idp'].admin_delete_user(UserPoolId=args.pool_id,Username=email)
            except Exception as exc: cleanup_errors.append({'kind':'user','errorType':type(exc).__name__})
        checks['testDataCleanupSucceeded'] = not cleanup_errors
        result['cleanupErrors'] = cleanup_errors
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,indent=2))
    return 0 if not result.get('errorType') and all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
