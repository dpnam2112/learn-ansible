-- Create a non-root user for your app
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY 'app_pw';
GRANT ALL PRIVILEGES ON appdb.* TO 'app'@'%';
FLUSH PRIVILEGES;

