#!/bin/bash
# FinSight EC2 Bootstrap Script
# Run this once after SSH-ing into a fresh Ubuntu 22.04 t3.large instance.
# Usage: chmod +x setup_ec2.sh && sudo ./setup_ec2.sh

set -euo pipefail   # exit on error, undefined var, or pipe failure

LOG_FILE="/var/log/finsight_setup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=============================================="
echo " FinSight EC2 Bootstrap — $(date)"
echo "=============================================="

# ── 1. System update ──────────────────────────────────────────────────────────
echo "[1/8] Updating system packages..."
apt-get update -y
apt-get upgrade -y

# ── 2. Install core tools ─────────────────────────────────────────────────────
echo "[2/8] Installing core tools..."
apt-get install -y \
    curl \
    wget \
    git \
    unzip \
    htop \
    net-tools \
    ca-certificates \
    gnupg \
    lsb-release \
    software-properties-common

# ── 3. Install Python 3.11 ────────────────────────────────────────────────────
echo "[3/8] Installing Python 3.11..."
add-apt-repository ppa:deadsnakes/ppa -y
apt-get update -y
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Alias python3 → python3.11
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
pip3 install --upgrade pip

# ── 4. Install Tesseract OCR + Poppler ───────────────────────────────────────
echo "[4/8] Installing Tesseract OCR and Poppler..."
apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libpq-dev

# Verify Tesseract
tesseract --version || { echo "ERROR: Tesseract install failed"; exit 1; }
echo "Tesseract installed at: $(which tesseract)"

# ── 5. Install AWS CLI v2 ────────────────────────────────────────────────────
echo "[5/8] Installing AWS CLI v2..."
if ! command -v aws &> /dev/null; then
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp/
    /tmp/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/aws
fi
aws --version

# ── 6. Install Docker ─────────────────────────────────────────────────────────
echo "[6/8] Installing Docker..."
if ! command -v docker &> /dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Add ubuntu user to docker group so we don't need sudo
usermod -aG docker ubuntu

# Enable and start Docker
systemctl enable docker
systemctl start docker
docker --version

# ── 7. Install Docker Compose (standalone) ───────────────────────────────────
echo "[7/8] Installing Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    COMPOSE_VERSION="2.27.0"
    curl -fsSL \
        "https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
        -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi
docker-compose --version

# ── 8. Clone repo and start application ──────────────────────────────────────
echo "[8/8] Setting up FinSight application..."

APP_DIR="/home/ubuntu/finsight"

if [ ! -d "$APP_DIR" ]; then
    # If repo not cloned yet, create the directory structure
    mkdir -p "$APP_DIR"
    echo "NOTE: Copy your finsight project files to $APP_DIR"
    echo "      Then create $APP_DIR/.env with your configuration"
    echo "      Then run: cd $APP_DIR && docker-compose up -d"
else
    # If the directory exists, start the services
    if [ -f "$APP_DIR/.env" ]; then
        echo "Starting FinSight services..."
        cd "$APP_DIR"
        docker-compose pull
        docker-compose up -d --build
        echo "Services started. Check status with: docker-compose ps"
    else
        echo "WARNING: $APP_DIR/.env not found."
        echo "Copy .env.example to .env and fill in your RDS/S3 values first."
    fi
fi

# ── CloudWatch Agent (optional but recommended) ───────────────────────────────
echo "Installing CloudWatch Agent for memory/disk metrics..."
wget -q https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb \
    -O /tmp/amazon-cloudwatch-agent.deb
dpkg -i /tmp/amazon-cloudwatch-agent.deb || true
rm /tmp/amazon-cloudwatch-agent.deb

# Write a minimal CloudWatch config
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CWCONFIG'
{
    "metrics": {
        "append_dimensions": {
            "InstanceId": "${aws:InstanceId}"
        },
        "metrics_collected": {
            "mem": {
                "measurement": ["mem_used_percent"],
                "metrics_collection_interval": 60
            },
            "disk": {
                "measurement": ["disk_used_percent"],
                "resources": ["/"],
                "metrics_collection_interval": 60
            }
        }
    }
}
CWCONFIG

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s || true

echo ""
echo "=============================================="
echo " Bootstrap complete! $(date)"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. cd /home/ubuntu/finsight"
echo "  2. cp .env.example .env && nano .env   # fill in RDS/S3 values"
echo "  3. docker-compose up -d --build"
echo "  4. docker-compose ps                   # verify all services running"
echo "  5. curl http://localhost:5000/health   # test the API"
echo ""
