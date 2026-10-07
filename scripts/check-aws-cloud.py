"""Verify real Cognito temporary-password and HTTP JWT enforcement; no emails sent.

Creates a disposable reserved-domain test user with MessageAction=SUPPRESS, then
deletes it in finally. Tokens/passwords never reach logs or evidence files.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys
import time
import boto3
import requests

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--pool-id', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    client = session.client('cognito-idp')
    username = f'ecoguard-smoke-{int(time.time())}@example.invalid'
    temporary, replacement = 'Aa!7'+secrets.token_hex(24), 'Bb!8'+secrets.token_hex(24)
    results = {'kind':'real-aws-cloud-configuration-check','emailVerificationTested':False,'emailsSent':0,'checks':{}}
    url = args.api_url.rstrip('/')+'/api/v1/media'
    created = False
    try:
        response = requests.get(url, timeout=30)
        results['checks']['anonymousRejected'] = response.status_code == 401
        client.admin_create_user(UserPoolId=args.pool_id, Username=username,
            TemporaryPassword=temporary, MessageAction='SUPPRESS',
            UserAttributes=[{'Name':k,'Value':v} for k,v in {'email':username,'given_name':'Cloud','family_name':'ConfigurationCheck'}.items()])
        created = True
        login = client.initiate_auth(ClientId=args.client_id, AuthFlow='USER_PASSWORD_AUTH', AuthParameters={'USERNAME':username,'PASSWORD':temporary})
        results['checks']['temporaryPasswordRequiresChange'] = login.get('ChallengeName') == 'NEW_PASSWORD_REQUIRED'
        if not results['checks']['temporaryPasswordRequiresChange']:
            raise RuntimeError('Expected mandatory password challenge')
        updated = client.respond_to_auth_challenge(ClientId=args.client_id, ChallengeName='NEW_PASSWORD_REQUIRED', Session=login['Session'], ChallengeResponses={'USERNAME':username,'NEW_PASSWORD':replacement})
        authentication = updated['AuthenticationResult']
        access = requests.get(url, headers={'Authorization':'Bearer '+authentication['AccessToken']}, timeout=30)
        results['checks']['accessTokenAccepted'] = access.status_code == 200 and isinstance(access.json().get('data'),dict)
        identity = requests.get(url, headers={'Authorization':'Bearer '+authentication['IdToken']}, timeout=30)
        results['checks']['idTokenRejected'] = identity.status_code == 401
        cors = requests.options(url, headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type,idempotency-key'}, timeout=30)
        allowed_headers = {value.strip().lower() for value in cors.headers.get('access-control-allow-headers','').split(',')}
        results['checks']['configuredCorsAllowed'] = cors.status_code in (200,204) and cors.headers.get('access-control-allow-origin') == 'http://127.0.0.1:5173' and {'authorization','content-type','idempotency-key'} <= allowed_headers
    finally:
        if created:
            client.admin_delete_user(UserPoolId=args.pool_id,Username=username)
            results['checks']['testUserDeleted'] = True
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(results,indent=2))
    return 0 if all(results['checks'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
