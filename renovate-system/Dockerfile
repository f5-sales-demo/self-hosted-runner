FROM renovate/renovate:44.52.1@sha256:6922c734849f2ed1c01e3a7924bd60e25e0ea904731a67bad72b02d563c0c3c3

USER root
RUN install -d -o 12021 -g 0 /opt/f5-renovate/containerbase \
    && cp -a /tmp/containerbase/. /opt/f5-renovate/containerbase/ \
    && chown -R 12021:0 /opt/f5-renovate/containerbase
COPY --chown=12021:0 --chmod=0555 github-app.mjs app-token-init.mjs token-entrypoint.mjs /opt/f5-renovate/
USER 12021:0
ENTRYPOINT ["node", "/opt/f5-renovate/token-entrypoint.mjs"]
