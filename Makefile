.DEFAULT_GOAL := build
ZIRAN ?= ziran
PREFIX ?= $(HOME)/.local

.PHONY: build check test run install window-test
build:
	$(ZIRAN) build
	cp scripts/api.py build/api.py
check:
	$(ZIRAN) check
test: check
	python3 -m unittest discover -s tests -v
window-test: build
	env -u DISPLAY -u WAYLAND_DISPLAY -u XAUTHORITY -u DBUS_SESSION_BUS_ADDRESS -u SESSION_MANAGER YUE_DESKTOP_RECOVERY=0 \
		xvfb-run -a -s '-screen 0 1280x1024x24' env GITHUB_PRIVATE_XVFB=1 python3 tests/window_test.py
run: build
	$(ZIRAN) run
install: build
	mkdir -p "$(PREFIX)/bin" "$(PREFIX)/lib/github" "$(PREFIX)/share/applications" "$(PREFIX)/share/icons/hicolor/scalable/apps"
	install -m 755 build/github-desktop "$(PREFIX)/lib/github/github-desktop"
	install -m 644 scripts/api.py "$(PREFIX)/lib/github/api.py"
	install -m 644 assets/github.svg "$(PREFIX)/share/icons/hicolor/scalable/apps/github-client.svg"
	printf '%s\n' '#!/bin/sh' 'exec "$(PREFIX)/lib/github/github-desktop" "$$@"' > "$(PREFIX)/bin/github-client"
	chmod 755 "$(PREFIX)/bin/github-client"
	printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=GitHub' 'Comment=Native GitHub client' 'Exec=$(PREFIX)/bin/github-client' 'Icon=github-client' 'Terminal=false' 'Categories=Development;' > "$(PREFIX)/share/applications/github-client.desktop"
