FROM hari31416/data-science-heavy-runtime:py312-v2@sha256:73520043dc5aa0a30475b8b8a11bf8cc9fea1236540c54b83055d9e876d32fec

# Build packages into the OCI rootfs before any network-disabled guest boots.
RUN python -m pip install --no-cache-dir duckdb==1.4.4 xlrd==2.0.2
