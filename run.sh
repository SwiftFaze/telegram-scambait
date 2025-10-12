#!/bin/bash
# Always use full paths in cron scripts

# Set working directory
cd /home/debian/telegram-scambait || exit 1

# Kill old instances
/usr/bin/ps aux | /usr/bin/grep '[m]ain.py' | /usr/bin/awk '{print $2}' | /usr/bin/xargs -r /bin/kill

# Start new instance in background
/usr/bin/nohup /usr/bin/python3 /home/debian/telegram-scambait/main.py > /dev/null 2>&1 &
