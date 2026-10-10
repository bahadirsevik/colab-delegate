export PATH=/root/.local/bin:$PATH
for c in upload download ssh status usage; do echo "=== $c ==="; colab $c --help 2>&1 | grep -v '^ *$' | head -22; done
