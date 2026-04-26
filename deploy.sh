#!/bin/bash
# Options Detective - Quick Deploy Script
# Run this on your Ubuntu 24.04 VPS

set -e  # Exit on error

echo "================================"
echo "Options Detective - Deployment"
echo "================================"
echo ""

# Check if running as root (not recommended)
if [ "$EUID" -eq 0 ]; then
    echo "⚠ Running as root. Consider creating a non-root user."
fi

# Update system
echo "📦 Updating system packages..."
apt-get update -qq

# Install Python/pip if needed
if ! command -v python3 &> /dev/null; then
    echo "📥 Installing Python 3..."
    apt-get install -y python3 python3-pip python3-venv
fi

# Install Docker if you want containerized deployment
if [ "$1" = "--docker" ]; then
    echo "🐳 Docker deployment selected"
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    systemctl start docker
    systemctl enable docker
    
    echo "Building and starting containers..."
    cd /tmp/options_detective
    docker-compose up -d
    
    echo ""
    echo "✅ Deployment complete!"
    echo "Dashboard: http://localhost:8000"
    echo "API docs: http://localhost:8000/docs"
    exit 0
fi

# Traditional deployment
echo "🐍 Setting up Python virtual environment..."
cd /tmp/options_detective

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate

echo "📚 Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Create .env if doesn't exist
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠ .env file created. Edit with your settings:"
    echo "   nano .env"
    echo ""
fi

# Initialize database
echo "🗄️  Initializing database..."
python -c "from src.database import create_tables; create_tables()"

# Create systemd service (optional)
if [ "$2" = "--service" ]; then
    echo "🔧 Creating systemd service..."
    sudo tee /etc/systemd/system/options-detective.service > /dev/null <<EOF
[Unit]
Description=Options Detective Trading Scanner
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=/tmp/options_detective
Environment=PATH=/tmp/options_detective/venv/bin
ExecStart=/tmp/options_detective/venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable options-detective
    sudo systemctl start options-detective
    echo "Service started: sudo systemctl status options-detective"
fi

echo ""
echo "✅ Deployment complete!"
echo ""
echo "🚀 Start the server:"
echo "   source venv/bin/activate"
echo "   uvicorn src.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo "🌐 Then visit: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "📖 See README.md for more options"
