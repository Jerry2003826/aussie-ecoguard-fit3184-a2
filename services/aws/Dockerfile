# Build from the repository root. Pin BASE_IMAGE to a verified digest for release.
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /var/task
COPY services/aws/requirements.lock.txt ./requirements.lock.txt
# The root packaging step must supply a pinned runtime-interface-client version.
ARG AWS_LAMBDA_RIC_VERSION
RUN test -n "$AWS_LAMBDA_RIC_VERSION" && pip install --no-cache-dir -r requirements.lock.txt "awslambdaric==${AWS_LAMBDA_RIC_VERSION}"
COPY packages/domain/ecoguard_domain ./ecoguard_domain
COPY services/aws/ecoguard_aws ./ecoguard_aws
ENV ECO_MODE=aws PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["ecoguard_aws.handlers.api"]
