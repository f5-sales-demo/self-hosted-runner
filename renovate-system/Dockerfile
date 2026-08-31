FROM renovate/renovate:44.52.1@sha256:6922c734849f2ed1c01e3a7924bd60e25e0ea904731a67bad72b02d563c0c3c3

USER root
COPY --chown=1001:1001 --chmod=0555 github-app.mjs app-token-init.mjs token-entrypoint.mjs /opt/f5-renovate/
USER 1001:1001
ENTRYPOINT ["node", "/opt/f5-renovate/token-entrypoint.mjs"]
