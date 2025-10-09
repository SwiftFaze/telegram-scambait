# Kill any old instances first, then start fresh
ps aux | grep '[m]ain.py' | awk '{print $2}' | xargs -r kill
nohup python3 /home/debian/telegram-scambait/main.py > /home/debian/telegram-scambait/main.log 2>&1 &
