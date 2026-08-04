#!/bin/bash
# SEGLC Schedule Analytics — Full deployment script for Ubuntu 22.04 Azure VM
# Run once after uploading the project folder to the VM
# Usage: bash deploy.sh

set -e   # stop on any error

APP_DIR="/home/azureuser/seglc_backend"
FRONTEND_DIR="$APP_DIR/frontend"

echo "======================================================"
echo "  SEGLC Schedule Analytics — Azure VM Deployment"
echo "======================================================"

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3-pip python3-venv nginx nodejs npm

# ── 2. Python virtual environment ────────────────────────────────────────────
echo "[2/8] Creating Python virtual environment..."
cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✓ Python packages installed"

# ── 3. Django setup ───────────────────────────────────────────────────────────
echo "[3/8] Running Django setup..."
mkdir -p logs
python manage.py migrate --run-syncdb --no-input
python manage.py collectstatic --no-input
echo "  ✓ Django migrations and static files done"

# ── 4. Build the React frontend ──────────────────────────────────────────────
echo "[4/8] Building React frontend..."
cd "$FRONTEND_DIR"
npm install --silent
npm run build
echo "  ✓ Frontend built → $FRONTEND_DIR/dist"
cd "$APP_DIR"

# ── 5. Gunicorn systemd service ───────────────────────────────────────────────
echo "[5/8] Installing Gunicorn service..."
sudo cp "$APP_DIR/deploy/seglc.service" /etc/systemd/system/seglc.service
sudo systemctl daemon-reload
sudo systemctl enable seglc
sudo systemctl restart seglc
sleep 2
sudo systemctl is-active seglc && echo "  ✓ Gunicorn running" || echo "  ✗ Gunicorn failed — check: sudo journalctl -u seglc -n 50"

# ── 6. Nginx configuration ────────────────────────────────────────────────────
echo "[6/8] Configuring Nginx..."
sudo cp "$APP_DIR/deploy/seglc.nginx" /etc/nginx/sites-available/seglc
sudo ln -sf /etc/nginx/sites-available/seglc /etc/nginx/sites-enabled/seglc
sudo rm -f /etc/nginx/sites-enabled/default     # remove the default placeholder
sudo nginx -t && echo "  ✓ Nginx config valid"
sudo systemctl restart nginx
sudo systemctl enable nginx
echo "  ✓ Nginx running"

# ── 7. Firewall ───────────────────────────────────────────────────────────────
echo "[7/8] Configuring UFW firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw --force enable
echo "  ✓ Firewall configured (SSH + HTTP/HTTPS open)"

# ── 8. Done ──────────────────────────────────────────────────────────────────
echo ""
echo "[8/8] Deployment complete!"
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │  Your app is live at:  http://$PUBLIC_IP  │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
echo "  Useful commands:"
echo "    Restart backend:  sudo systemctl restart seglc"
echo "    Backend logs:     sudo journalctl -u seglc -f"
echo "    Nginx logs:       sudo tail -f /var/log/nginx/error.log"
echo "    Redeploy app:     cd $APP_DIR && bash deploy/deploy.sh"
echo ""
