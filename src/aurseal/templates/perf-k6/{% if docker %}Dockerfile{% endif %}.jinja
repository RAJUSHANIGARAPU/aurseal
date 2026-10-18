# The k6 version CI installs (.github/workflows/ci.yml), so a local run measures the same way.
FROM grafana/k6:2.3.0

WORKDIR /app
COPY k6/ k6/

# Reports go to a bind-mounted directory; uid 1000 matches the usual first user on a
# Linux host, so the files stay writable there.
USER root
RUN mkdir reports && chown 1000 reports
USER 1000

ENTRYPOINT ["k6"]
CMD ["run", "k6/scenarios/smoke.js"]
