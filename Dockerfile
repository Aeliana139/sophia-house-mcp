FROM python:3.11-slim
WORKDIR /app
COPY mcp_server.py .
# Glama's runner has no house loopback: the server speaks ONLY to the public doors.
ENV SOCSEAL_BASE=https://socseal.xyz
ENV BOOK_BASE=https://socseal.xyz
ENV KEEPER_BASE=https://socseal.xyz
CMD ["python3", "-u", "mcp_server.py"]
