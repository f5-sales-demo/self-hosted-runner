# syntax=docker/dockerfile:1.7
# Build only in the GitHub-hosted workflows in .github/workflows.
ARG UBUNTU_IMAGE=docker.io/library/ubuntu@sha256:d78ab76437b1afc5f01e223d6bf0172763f404bb166441328845adbef44518cb
ARG NODE_IMAGE=docker.io/library/node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
ARG DOCKER_CLI_IMAGE=docker.io/library/docker@sha256:000bb62ff495f986c9f5578eb67cc2cb98b91138eda81d7762d5371eb8a497fe

FROM ${DOCKER_CLI_IMAGE} AS docker-cli
FROM ${NODE_IMAGE} AS node-cli

FROM ${UBUNTU_IMAGE} AS runner-base
# Bootstrap the CA bundle from the pinned Docker CLI stage before contacting the signed Ubuntu snapshot.
COPY --from=docker-cli /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

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
ARG RUFF_VERSION=0.16.0
ARG RUFF_SHA256=98001c995a134d95f9bc83106a7f94b552971b583f1c0ab75fb656a881e13865
ARG GCLOUD_VERSION=579.0.0
ARG GCLOUD_SHA256=a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de
ARG KUBECTL_VERSION=1.36.3
ARG KUBECTL_SHA256=ebbd080e7c2e275093b55915722043257eb24004363e20acb3c4d71919f88336
ARG KUSTOMIZE_VERSION=5.8.1
ARG KUSTOMIZE_SHA256=029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d
ARG BUN_VERSION=1.3.11
ARG BUN_SHA256=8611ba935af886f05a6f38740a15160326c15e5d5d07adef966130b4493607ed
ARG TERRAFORM_VERSION=1.15.8
ARG TERRAFORM_SHA256=d25ce7b6902013ad905db3d2eab0be4cd905887fe88b81a6171b8d5503c31f3d
ARG NODE20_VERSION=20.19.6
ARG NODE20_SHA256=c514127107ebf6e3885f793b06674574d71fe22e3df91a78c52c5a6f84b3b5b0
ARG NODE24_14_VERSION=24.14.1
ARG NODE24_14_SHA256=84d38715d449447117d05c3e71acd78daa49d5b1bfa8aacf610303920c3322be
ARG NODE24_19_VERSION=24.19.0
ARG NODE24_19_SHA256=14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647
ARG PYTHON311_VERSION=3.11.13
ARG PYTHON311_SHA256=bf58712e13464122707e63d8913f17aaeff63293ee4c0cee11ce9f76b188fab6
ARG PYTHON313_VERSION=3.13.7
ARG PYTHON313_SHA256=06962ffec2157889b6edaddcfc47e96c3b002a55db076adcd0144d7d7a3b716d
ARG CODEX_VERSION=0.148.0
ARG CODEX_SHA256=52f1b8f5cf66fe776d2bd5e68a32ef6884b7e466fd80614bf8858046dfa9012f
ARG CLAUDE_CODE_VERSION=2.1.236
ARG CLAUDE_CODE_SHA256=2414f2c35505033e1cc8e50f0d7f3c49fe1af180820a236b1d4733bca4c7bc9d
ARG OPENCODE_VERSION=1.18.18
ARG OPENCODE_SHA256=0cddc222418b8553669905a8980c0cda7088f00da24d83d6ac76b01c9fdb2aaf
ARG AGY_VERSION=1.1.15
ARG AGY_RELEASE=1.1.15-5350383476932608
ARG AGY_SHA256=d0b1d6f3678a061915caebc431930e240b863bf4059369c08c6ffceb24e66b5f
ARG XCSH_VERSION=20.20.3
ARG XCSH_SHA256=cb5c51e0912b7beec947f319049e7ec862a656e816fc1e2fbc1b36a060c51c8d

ARG TFPLUGINDOCS_VERSION=0.25.0
ARG TFPLUGINDOCS_SHA256=912bd663e2deafc9ebf54e932bd2adf91bf6b7fcf545d4d9a82dc9597255854c
ARG GOLANGCI_LINT_VERSION=2.12.2
ARG GOLANGCI_LINT_SHA256=8df580d2670fed8fa984aac0507099af8df275e665215f5c7a2ae3943893a553
ARG GOVULNCHECK_VERSION=1.6.0
ARG GOVULNCHECK_MODULE_SUM=h1:FeMO9Rm/HwyduOztbvKcOw+zvDEPr4I4aQNSfevFcKY=
ENV DEBIAN_FRONTEND=noninteractive \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    DOTNET_ROOT=/opt/dotnet \
    POWERSHELL_TELEMETRY_OPTOUT=1 \
    PATH=/opt/go/bin:/opt/dotnet:/opt/powershell:/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:${PATH}

# The snapshot fixes the complete apt package set. No service is enabled or started here; every target runs as the unprivileged runner user.
RUN rm -f /etc/apt/sources.list.d/* \
    && { echo "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} noble main restricted universe multiverse"; echo "deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT} noble-updates main restricted universe multiverse"; } > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 install --yes --no-install-recommends \
      ant bash build-essential cargo composer bzip2 ca-certificates cmake curl dbus-x11 default-mysql-client \
      dnsutils dpkg-dev file fonts-liberation git git-lfs gnupg gpg iproute2 iputils-ping jq libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libcups2t64 libdrm2 libgbm1 libgtk-3-0 libicu74 \
      libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libsecret-1-0 libssl3 locales make maven mercurial netcat-openbsd openjdk-17-jdk openjdk-21-jdk p7zip-full \
      php-cli php-curl php-mbstring php-xml pipx pkg-config postgresql-client python-is-python3 \
      python3 python3-dev python3-keyring python3-pip python3-venv python3-yaml ruby-full rustc shellcheck \
      sqlite3 sudo swig unzip wget xz-utils zip zstd \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 --shell /bin/bash runner \
    && install -d -o runner -g runner /opt/actions-runner /runner-runtime "$AGENT_TOOLSDIRECTORY"

ARG PNPM_VERSION=11.3.0
ARG PNPM_SHA256=5ade1ef51cf36441f4a00931eaf9003654689eba3684939f70d7576b2dfb8474
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
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/bun.zip "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-x64.zip"; \
    echo "${BUN_SHA256}  /tmp/bun.zip" | sha256sum --check --strict; unzip -q /tmp/bun.zip -d /opt && ln -s /opt/bun-linux-x64/bun /usr/local/bin/bun; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/terraform.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip"; \
    echo "${TERRAFORM_SHA256}  /tmp/terraform.zip" | sha256sum --check --strict; unzip -q /tmp/terraform.zip -d /usr/local/bin; \
curl --fail --location --proto =https --tlsv1.2 --output /tmp/node20.tar.xz "https://nodejs.org/dist/v${NODE20_VERSION}/node-v${NODE20_VERSION}-linux-x64.tar.xz"; \
    echo "${NODE20_SHA256}  /tmp/node20.tar.xz" | sha256sum --check --strict; tar --extract --xz --file /tmp/node20.tar.xz --directory /opt; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/node24-14.tar.xz "https://nodejs.org/dist/v${NODE24_14_VERSION}/node-v${NODE24_14_VERSION}-linux-x64.tar.xz"; \
    echo "${NODE24_14_SHA256}  /tmp/node24-14.tar.xz" | sha256sum --check --strict; tar --extract --xz --file /tmp/node24-14.tar.xz --directory /opt; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/node24-19.tar.xz "https://nodejs.org/dist/v${NODE24_19_VERSION}/node-v${NODE24_19_VERSION}-linux-x64.tar.xz"; \
    echo "${NODE24_19_SHA256}  /tmp/node24-19.tar.xz" | sha256sum --check --strict; tar --extract --xz --file /tmp/node24-19.tar.xz --directory /opt; \
    mkdir -p /opt/python-${PYTHON311_VERSION} /opt/python-${PYTHON313_VERSION}; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/python311.tar.gz "https://github.com/actions/python-versions/releases/download/3.11.13-15433298024/python-${PYTHON311_VERSION}-linux-24.04-x64.tar.gz"; \
    echo "${PYTHON311_SHA256}  /tmp/python311.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/python311.tar.gz --directory /opt/python-${PYTHON311_VERSION}; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/python313.tar.gz "https://github.com/actions/python-versions/releases/download/3.13.7-16980743123/python-${PYTHON313_VERSION}-linux-24.04-x64.tar.gz"; \
    echo "${PYTHON313_SHA256}  /tmp/python313.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/python313.tar.gz --directory /opt/python-${PYTHON313_VERSION}; \
curl --fail --location --proto =https --tlsv1.2 --output /tmp/codex.tgz "https://registry.npmjs.org/@openai/codex/-/codex-${CODEX_VERSION}-linux-x64.tgz"; \
    echo "${CODEX_SHA256}  /tmp/codex.tgz" | sha256sum --check --strict; mkdir -p /opt/codex && tar --extract --gzip --file /tmp/codex.tgz --directory /opt/codex && ln -s /opt/codex/package/vendor/x86_64-unknown-linux-musl/bin/codex /usr/local/bin/codex; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/claude-code.tgz "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-${CLAUDE_CODE_VERSION}.tgz"; \
    echo "${CLAUDE_CODE_SHA256}  /tmp/claude-code.tgz" | sha256sum --check --strict; mkdir -p /opt/claude-code && tar --extract --gzip --file /tmp/claude-code.tgz --directory /opt/claude-code && install -m 0555 /opt/claude-code/package/claude /usr/local/bin/claude; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/opencode.tar.gz "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"; \
    echo "${OPENCODE_SHA256}  /tmp/opencode.tar.gz" | sha256sum --check --strict; mkdir -p /opt/opencode && tar --extract --gzip --file /tmp/opencode.tar.gz --directory /opt/opencode && install -m 0555 /opt/opencode/opencode /usr/local/bin/opencode; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/agy.tar.gz "https://storage.googleapis.com/antigravity-public/antigravity-cli/${AGY_RELEASE}/linux-x64/cli_linux_x64.tar.gz"; \
    echo "${AGY_SHA256}  /tmp/agy.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/agy.tar.gz --directory /tmp antigravity && install -m 0555 /tmp/antigravity /usr/local/bin/agy; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/xcsh "https://github.com/f5-sales-demo/xcsh/releases/download/v${XCSH_VERSION}/xcsh-linux-x64"; \
    echo "${XCSH_SHA256}  /tmp/xcsh" | sha256sum --check --strict; install -m 0555 /tmp/xcsh /usr/local/bin/xcsh; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/pnpm.tgz "https://registry.npmjs.org/pnpm/-/pnpm-${PNPM_VERSION}.tgz"; \
    echo "${PNPM_SHA256}  /tmp/pnpm.tgz" | sha256sum --check --strict; mkdir -p /opt/pnpm && tar --extract --gzip --file /tmp/pnpm.tgz --directory /opt/pnpm && rm -f /tmp/pnpm.tgz; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip"; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/tfplugindocs.zip "https://github.com/hashicorp/terraform-plugin-docs/releases/download/v${TFPLUGINDOCS_VERSION}/tfplugindocs_${TFPLUGINDOCS_VERSION}_linux_amd64.zip"; \
    echo "${TFPLUGINDOCS_SHA256}  /tmp/tfplugindocs.zip" | sha256sum --check --strict; unzip -q /tmp/tfplugindocs.zip -d /tmp/tfplugindocs && install -m 0555 "$(find /tmp/tfplugindocs -type f -name tfplugindocs -print -quit)" /usr/local/bin/tfplugindocs; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/golangci-lint.tar.gz "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-amd64.tar.gz"; \
    echo "${GOLANGCI_LINT_SHA256}  /tmp/golangci-lint.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/golangci-lint.tar.gz --directory /tmp && install -m 0555 "/tmp/golangci-lint-${GOLANGCI_LINT_VERSION}-linux-amd64/golangci-lint" /usr/local/bin/golangci-lint; \
    test "$(GOTOOLCHAIN=local go mod download -json "golang.org/x/vuln@v${GOVULNCHECK_VERSION}" | jq -r .Sum)" = "${GOVULNCHECK_MODULE_SUM}"; \
    GOTOOLCHAIN=local GOBIN=/usr/local/bin go install "golang.org/x/vuln/cmd/govulncheck@v${GOVULNCHECK_VERSION}"; \
    test -x /usr/local/bin/govulncheck; \
    echo "${AWSCLI_SHA256}  /tmp/awscliv2.zip" | sha256sum --check --strict; unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --bin-dir /usr/local/bin --install-dir /opt/aws-cli; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/helm.tar.gz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz"; \
    echo "${HELM_SHA256}  /tmp/helm.tar.gz" | sha256sum --check --strict; tar --extract --gzip --file /tmp/helm.tar.gz --directory /tmp && install -m 0555 /tmp/linux-amd64/helm /usr/local/bin/helm; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/android-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_TOOLS_REVISION}_latest.zip"; \
    echo "${ANDROID_TOOLS_SHA256}  /tmp/android-tools.zip" | sha256sum --check --strict; mkdir -p /opt/android-sdk/cmdline-tools/latest && unzip -q /tmp/android-tools.zip -d /tmp/android-tools && mv /tmp/android-tools/cmdline-tools/* /opt/android-sdk/cmdline-tools/latest/; \
    rm -rf /tmp/gh.tar.gz /tmp/gh_* /tmp/actions-runner.tar.gz /tmp/go.tar.gz /tmp/dotnet.tar.gz /tmp/powershell.tar.gz /tmp/gcloud.tar.gz /tmp/kubectl /tmp/kustomize.tar.gz /tmp/kustomize /tmp/bun.zip /tmp/terraform.zip /tmp/node20.tar.xz /tmp/node24-14.tar.xz /tmp/node24-19.tar.xz /tmp/python311.tar.gz /tmp/python313.tar.gz /tmp/codex.tgz /tmp/claude-code.tgz /tmp/opencode.tar.gz /tmp/agy.tar.gz /tmp/antigravity /tmp/xcsh /tmp/tfplugindocs.zip /tmp/tfplugindocs /tmp/golangci-lint.tar.gz /tmp/golangci-lint-* /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu /tmp/biome /tmp/awscliv2.zip /tmp/aws /tmp/chrome.zip /tmp/chromedriver.zip /tmp/geckodriver.tar.gz /tmp/helm.tar.gz /tmp/linux-amd64 /tmp/android-tools.zip /tmp/android-tools; \
    chown -R runner:runner /opt/actions-runner /opt/go /opt/dotnet /opt/powershell /opt/android-sdk /opt/chrome-linux64 /opt/chromedriver-linux64 /opt/google-cloud-sdk /opt/node-v* /opt/python-* /opt/codex /opt/claude-code /opt/opencode

COPY --from=node-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=node-cli /usr/local/lib/libnode.so.* /usr/local/lib/
COPY --from=node-cli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && install -d -o runner -g runner "$AGENT_TOOLSDIRECTORY/node/20.19.6" "$AGENT_TOOLSDIRECTORY/node/22.23.2" "$AGENT_TOOLSDIRECTORY/node/24.14.1" "$AGENT_TOOLSDIRECTORY/node/24.19.0" "$AGENT_TOOLSDIRECTORY/Go/1.25.12" "$AGENT_TOOLSDIRECTORY/Python/3.11.13" "$AGENT_TOOLSDIRECTORY/Python/3.12.3" "$AGENT_TOOLSDIRECTORY/Python/3.13.7" \
    && ln -s /opt/node-v20.19.6-linux-x64 "$AGENT_TOOLSDIRECTORY/node/20.19.6/x64" \
    && ln -s /usr/local "$AGENT_TOOLSDIRECTORY/node/22.23.2/x64" \
    && ln -s /opt/node-v24.14.1-linux-x64 "$AGENT_TOOLSDIRECTORY/node/24.14.1/x64" \
    && ln -s /opt/node-v24.19.0-linux-x64 "$AGENT_TOOLSDIRECTORY/node/24.19.0/x64" \
    && ln -s /opt/go "$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64" \
    && ln -s /opt/python-3.11.13 "$AGENT_TOOLSDIRECTORY/Python/3.11.13/x64" \
    && ln -s /usr "$AGENT_TOOLSDIRECTORY/Python/3.12.3/x64" \
    && ln -s /opt/python-3.13.7 "$AGENT_TOOLSDIRECTORY/Python/3.13.7/x64" \
    && touch "$AGENT_TOOLSDIRECTORY/node/20.19.6/x64.complete" "$AGENT_TOOLSDIRECTORY/node/22.23.2/x64.complete" "$AGENT_TOOLSDIRECTORY/node/24.14.1/x64.complete" "$AGENT_TOOLSDIRECTORY/node/24.19.0/x64.complete" "$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64.complete" "$AGENT_TOOLSDIRECTORY/Python/3.11.13/x64.complete" "$AGENT_TOOLSDIRECTORY/Python/3.12.3/x64.complete" "$AGENT_TOOLSDIRECTORY/Python/3.13.7/x64.complete" \
    && chown -R runner:runner "$AGENT_TOOLSDIRECTORY"

RUN printf '%s\n' '#!/bin/sh' 'exec node /opt/pnpm/package/bin/pnpm.cjs "$@"' > /usr/local/bin/pnpm \
    && chmod 0555 /usr/local/bin/pnpm \
    && chown -R runner:runner /opt/pnpm

COPY --chown=root:root catalog/spectral-package.json /opt/spectral/package.json
COPY --chown=root:root catalog/spectral-package-lock.json /opt/spectral/package-lock.json
RUN cd /opt/spectral \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -s /opt/spectral/node_modules/.bin/spectral /usr/local/bin/spectral \
    && chown -R runner:runner /opt/spectral

RUN set -eux; \
    install -d -o runner -g runner "$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64"; \
    ln -s /usr/local/bin/uv "$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64/uv"; \
    ln -s /usr/local/bin/uvx "$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64/uvx"; \
    touch "$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64.complete"; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/ruff.tar.gz "https://github.com/astral-sh/ruff/releases/download/${RUFF_VERSION}/ruff-x86_64-unknown-linux-gnu.tar.gz"; \
    echo "${RUFF_SHA256}  /tmp/ruff.tar.gz" | sha256sum --check --strict; \
    install -d -o runner -g runner "$AGENT_TOOLSDIRECTORY/ruff/${RUFF_VERSION}/x86_64"; \
    tar --extract --gzip --file /tmp/ruff.tar.gz --directory "$AGENT_TOOLSDIRECTORY/ruff/${RUFF_VERSION}/x86_64" --strip-components=1; \
    touch "$AGENT_TOOLSDIRECTORY/ruff/${RUFF_VERSION}/x86_64.complete"; \
    rm -f /tmp/ruff.tar.gz
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
