# syntax=docker/dockerfile:1.7
# Build only in the GitHub-hosted workflows in .github/workflows.
ARG UBUNTU_IMAGE=docker.io/library/ubuntu@sha256:d78ab76437b1afc5f01e223d6bf0172763f404bb166441328845adbef44518cb
ARG NODE_IMAGE=docker.io/library/node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
ARG DOCKER_CLI_IMAGE=docker.io/library/docker@sha256:000bb62ff495f986c9f5578eb67cc2cb98b91138eda81d7762d5371eb8a497fe

FROM ${DOCKER_CLI_IMAGE} AS docker-cli
FROM ${NODE_IMAGE} AS node-cli

FROM ${UBUNTU_IMAGE} AS runner-base

ARG RUNNER_VERSION=2.336.0
ARG RUNNER_SHA256=04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d
ARG GH_VERSION=2.97.0
ARG GH_SHA256=a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112
ARG GO_VERSION=1.25.12
ARG GO_SHA256=234828b7a89e0e303d2556310ee549fbcf253d28de937bac3da13d6294262ac1
ARG DOTNET_VERSION=10.0.302
ARG DOTNET_SHA256=264a838d6f5d1a252489c7bb2e2946a579d6a881391d50ffd175a01e4d948c1c
ARG POWERSHELL_VERSION=7.6.4
ARG POWERSHELL_SHA256=4471b5a36bfe86ec7af8525d36bb1cacba0128e7aac22d05cc064bc00e604721
ARG AWSCLI_VERSION=2.36.20
ARG AWSCLI_SHA256=59bdfab4035b0251a0c8de801abe01928861a89e27433bb80fc3fcf6dfe32352
ARG HELM_VERSION=3.21.3
ARG HELM_SHA256=15e041a93a590dce8100f39385cd98c84a765c9e36aeeb9e2dc6ff9e4769e2e0
ARG ANDROID_TOOLS_REVISION=11076708
ARG ANDROID_TOOLS_SHA256=2d2d50857e4eb553af5a6dc3ad507a17adf43d115264b1afc116f95c92e5e258
ARG APT_SNAPSHOT=20260810T000000Z
ARG CHROME_VERSION=151.0.7922.77
ARG CHROME_SHA256=60a324a6e1d27b20f2035a2cdaf71641a739fe1f5571f63794773225820bce8a
ARG CHROMEDRIVER_VERSION=151.0.7922.77
ARG CHROMEDRIVER_SHA256=65dca829d176845f864b794be1ecdd31e855f14ca9fa1eb93b2d1c0e7242abdd
ARG GECKODRIVER_VERSION=0.37.1
ARG GECKODRIVER_SHA256=e815130ea95983e162ae91843b48d3a3ce991735635fce83a647afde21e09f7e
ARG UV_VERSION=0.8.24
ARG UV_SHA256=db8179fffd97b7557b9a519bae82eaa4f499b02ef546f738a35e74e26c47e6b7
ARG BIOME_VERSION=2.5.6
ARG BIOME_SHA256=3cc9a0c3fa26ac26a89e8a3b203c010c9ae88e36f69a2679e79981f267ce9d57
ARG GCLOUD_VERSION=579.0.0
ARG GCLOUD_SHA256=a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de
ARG KUBECTL_VERSION=1.36.3
ARG KUBECTL_SHA256=ebbd080e7c2e275093b55915722043257eb24004363e20acb3c4d71919f88336
ARG KUSTOMIZE_VERSION=5.8.1
ARG KUSTOMIZE_SHA256=029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d

ENV DEBIAN_FRONTEND=noninteractive \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    DOTNET_ROOT=/opt/dotnet \
    POWERSHELL_TELEMETRY_OPTOUT=1 \
    PATH=/opt/go/bin:/opt/dotnet:/opt/powershell:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:${PATH}

# The snapshot fixes the complete apt package set. No service is enabled or started here; every target runs as the unprivileged runner user.
RUN { echo "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} noble main restricted universe multiverse"; echo "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} noble-updates main restricted universe multiverse"; } > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
      ant bash build-essential cargo composer bzip2 ca-certificates cmake curl dbus-x11 default-mysql-client \
      dnsutils dpkg-dev file fonts-liberation git git-lfs gnupg gpg iproute2 iputils-ping jq libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libcups2t64 libdrm2 libgbm1 libgtk-3-0 libicu74 \
      libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libsecret-1-0 libssl3 locales make maven mercurial netcat-openbsd openjdk-21-jdk p7zip-full \
      php-cli php-curl php-mbstring php-xml pipx pkg-config postgresql-client python-is-python3 \
      python3 python3-dev python3-keyring python3-pip python3-venv python3-yaml ruby-full rustc shellcheck \
      sqlite3 sudo swig unzip wget xz-utils zip zstd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 --shell /bin/bash runner \
    && install -d -o runner -g runner /opt/actions-runner /runner-runtime "$AGENT_TOOLSDIRECTORY"

RUN set -eux; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/gh.tar.gz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz"; \
    echo "${GH_SHA256}  /tmp/gh.tar.gz" | sha256sum --check --strict; \
    tar --extract --gzip --file /tmp/gh.tar.gz --directory /tmp; install -m 0555 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/actions-runner.tar.gz "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"; \
    echo "${RUNNER_SHA256}  /tmp/actions-runner.tar.gz" | sha256sum --check --strict; \
    tar --extract --gzip --file /tmp/actions-runner.tar.gz --directory /opt/actions-runner; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/go.tar.gz "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz"; \
    echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/go.tar.gz --directory /opt; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/dotnet.tar.gz "https://builds.dotnet.microsoft.com/dotnet/Sdk/${DOTNET_VERSION}/dotnet-sdk-${DOTNET_VERSION}-linux-x64.tar.gz"; \
    echo "${DOTNET_SHA256}  /tmp/dotnet.tar.gz" | sha256sum --check --strict; mkdir -p /opt/dotnet && tar --extract --gzip --file /tmp/dotnet.tar.gz --directory /opt/dotnet; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/powershell.tar.gz "https://github.com/PowerShell/PowerShell/releases/download/v${POWERSHELL_VERSION}/powershell-${POWERSHELL_VERSION}-linux-x64.tar.gz"; \
    echo "${POWERSHELL_SHA256}  /tmp/powershell.tar.gz" | sha256sum --check --strict; mkdir -p /opt/powershell && tar --extract --gzip --file /tmp/powershell.tar.gz --directory /opt/powershell; \
    chmod 0555 /opt/powershell/pwsh && ln -s /opt/powershell/pwsh /usr/local/bin/pwsh; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/chrome.zip \
      "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip"; \
    echo "${CHROME_SHA256}  /tmp/chrome.zip" | sha256sum --check --strict; unzip -q /tmp/chrome.zip -d /opt && ln -s /opt/chrome-linux64/chrome /usr/local/bin/google-chrome; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/chromedriver.zip \
      "https://storage.googleapis.com/chrome-for-testing-public/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip"; \
    echo "${CHROMEDRIVER_SHA256}  /tmp/chromedriver.zip" | sha256sum --check --strict; unzip -q /tmp/chromedriver.zip -d /opt && ln -s /opt/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/geckodriver.tar.gz \
      "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz"; \
    echo "${GECKODRIVER_SHA256}  /tmp/geckodriver.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/geckodriver.tar.gz --directory /usr/local/bin; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/uv.tar.gz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"; \
    echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/uv.tar.gz --directory /tmp && install -m 0555 /tmp/uv-x86_64-unknown-linux-gnu/uv /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/biome "https://github.com/biomejs/biome/releases/download/%40biomejs/biome%40${BIOME_VERSION}/biome-linux-x64"; \
    echo "${BIOME_SHA256}  /tmp/biome" | sha256sum --check --strict; install -m 0555 /tmp/biome /usr/local/bin/biome; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/gcloud.tar.gz "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${GCLOUD_VERSION}-linux-x86_64.tar.gz"; \
    echo "${GCLOUD_SHA256}  /tmp/gcloud.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/gcloud.tar.gz --directory /opt && ln -s /opt/google-cloud-sdk/bin/gcloud /usr/local/bin/gcloud; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/kubectl "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl"; \
    echo "${KUBECTL_SHA256}  /tmp/kubectl" | sha256sum --check --strict; install -m 0555 /tmp/kubectl /usr/local/bin/kubectl; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/kustomize.tar.gz "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"; \
    echo "${KUSTOMIZE_SHA256}  /tmp/kustomize.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/kustomize.tar.gz --directory /tmp && install -m 0555 /tmp/kustomize /usr/local/bin/kustomize; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip"; \
    echo "${AWSCLI_SHA256}  /tmp/awscliv2.zip" | sha256sum --check --strict; unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --bin-dir /usr/local/bin --install-dir /opt/aws-cli; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/helm.tar.gz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz"; \
    echo "${HELM_SHA256}  /tmp/helm.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/helm.tar.gz --directory /tmp && install -m 0555 /tmp/linux-amd64/helm /usr/local/bin/helm; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/android-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_TOOLS_REVISION}_latest.zip"; \
    echo "${ANDROID_TOOLS_SHA256}  /tmp/android-tools.zip" | sha256sum --check --strict; mkdir -p /opt/android-sdk/cmdline-tools/latest && unzip -q /tmp/android-tools.zip -d /tmp/android-tools && mv /tmp/android-tools/cmdline-tools/* /opt/android-sdk/cmdline-tools/latest/; \
    rm -rf /tmp/gh.tar.gz /tmp/gh_* /tmp/actions-runner.tar.gz /tmp/go.tar.gz /tmp/dotnet.tar.gz /tmp/powershell.tar.gz /tmp/gcloud.tar.gz /tmp/kubectl /tmp/kustomize.tar.gz /tmp/kustomize /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu /tmp/biome /tmp/awscliv2.zip /tmp/aws /tmp/chrome.zip /tmp/chromedriver.zip /tmp/geckodriver.tar.gz /tmp/helm.tar.gz /tmp/linux-amd64 /tmp/android-tools.zip /tmp/android-tools; \
    chown -R runner:runner /opt/actions-runner /opt/go /opt/dotnet /opt/powershell /opt/android-sdk /opt/chrome-linux64 /opt/chromedriver-linux64 /opt/google-cloud-sdk

COPY --from=node-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=node-cli /usr/local/lib/libnode.so.* /usr/local/lib/
COPY --from=node-cli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && install -d -o runner -g runner "$AGENT_TOOLSDIRECTORY/node/22.23.2" "$AGENT_TOOLSDIRECTORY/Go/1.25.12" "$AGENT_TOOLSDIRECTORY/Python/3.12.3" \
    && ln -s /usr/local "$AGENT_TOOLSDIRECTORY/node/22.23.2/x64" \
    && ln -s /opt/go "$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64" \
    && ln -s /usr "$AGENT_TOOLSDIRECTORY/Python/3.12.3/x64" \
    && touch "$AGENT_TOOLSDIRECTORY/node/22.23.2/x64.complete" "$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64.complete" "$AGENT_TOOLSDIRECTORY/Python/3.12.3/x64.complete" \
    && chown -R runner:runner "$AGENT_TOOLSDIRECTORY"

COPY --chown=root:root scripts/runner-entrypoint.sh /usr/local/bin/runner-entrypoint
COPY --chown=root:root scripts/verify-tools.py /usr/local/bin/verify-runner-tools
COPY --chown=root:root catalog/tool-catalog.json /usr/local/share/runner-catalog/tool-catalog.json
RUN chmod 0555 /usr/local/bin/runner-entrypoint /usr/local/bin/verify-runner-tools

WORKDIR /runner-runtime

FROM runner-base AS standard
USER runner
ENTRYPOINT ["/usr/local/bin/runner-entrypoint"]
LABEL org.opencontainers.image.source="https://github.com/f5-sales-demo/self-hosted-runner" \
      org.opencontainers.image.description="Ephemeral socketless GitHub Actions runner" \
      f5.sales-demo.runner.profile="standard"

FROM runner-base AS container-build
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
USER runner
ENTRYPOINT ["/usr/local/bin/runner-entrypoint"]
LABEL org.opencontainers.image.source="https://github.com/f5-sales-demo/self-hosted-runner" \
      org.opencontainers.image.description="Ephemeral Docker-client runner; host socket is trust-gated by docs-control" \
      f5.sales-demo.runner.profile="container-build"
