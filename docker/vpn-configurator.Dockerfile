FROM ubuntu:22.04

# Port 80 is blocked so use HTTPS; install ca-certificates first (with
# verification temporarily disabled since the minimal image lacks certs).
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list && \
    apt-get -o Acquire::https::Verify-Peer=false update && \
    apt-get -o Acquire::https::Verify-Peer=false install -y ca-certificates && \
    apt-get update && \
    apt-get install -y iproute2 iptables && \
    rm -rf /var/lib/apt/lists/*