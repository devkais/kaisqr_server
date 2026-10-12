module.exports = {
  apps: [
    {
      name: "qr-server",
      cwd: __dirname,
      script: ".venv\\Scripts\\python.exe",
      interpreter: "none",
      args: "-m app.main",
      autorestart: true,
    },
  ],
};
