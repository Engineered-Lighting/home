FROM node:24-alpine@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd

WORKDIR /app
COPY package.json server.mjs ./
COPY src ./src
COPY --from=agent_assets index.html /srv/home-agent/index.html
COPY --from=agent_assets api.js /srv/home-agent/api.js
COPY --from=agent_assets panel.js /srv/home-agent/panel.js
COPY --from=agent_assets panel.css /srv/home-agent/panel.css
COPY --from=agent_assets preference-review.html /srv/home-agent/preference-review.html
COPY --from=agent_assets preference-review.js /srv/home-agent/preference-review.js
COPY --from=agent_assets preference-review.css /srv/home-agent/preference-review.css
COPY --from=agent_assets lighting-review.html /srv/home-agent/lighting-review.html
COPY --from=agent_assets lighting-review.js /srv/home-agent/lighting-review.js

USER node
EXPOSE 8096
CMD ["node", "server.mjs"]
