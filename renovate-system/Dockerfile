FROM renovate/renovate:45.1.1@sha256:42eb22bccd07a3a9c9ba59e9de8d0b17005fcb6b775b2b7eed7cb3678dfc9068

USER root
RUN install -d -o 12021 -g 0 \
      /opt/f5-renovate/containerbase \
      /opt/f5-renovate/containerbase-runtime \
    && cp -a /tmp/containerbase/. /opt/f5-renovate/containerbase/ \
    && cp -a /opt/containerbase/. /opt/f5-renovate/containerbase-runtime/ \
    && chown -R 12021:0 \
      /opt/f5-renovate/containerbase \
      /opt/f5-renovate/containerbase-runtime
COPY --chown=12021:0 --chmod=0555 github-app.mjs app-token-init.mjs token-entrypoint.mjs /opt/f5-renovate/
USER 12021:0
ENTRYPOINT ["node", "/opt/f5-renovate/token-entrypoint.mjs"]
