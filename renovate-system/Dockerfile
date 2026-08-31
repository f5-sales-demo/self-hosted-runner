FROM renovate/renovate@sha256:f3af0e38b415d50c881139c721c13956bd4a11b804f7198275bcc3c501dea6c1
USER root
COPY --chown=1001:1001 token-entrypoint.mjs /usr/local/bin/renovate-token-entrypoint.mjs
USER 1001
ENTRYPOINT ["node", "/usr/local/bin/renovate-token-entrypoint.mjs"]
