PYTHON ?= python3
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
NO_PROXY_ENV := env -u ALL_PROXY -u HTTPS_PROXY -u HTTP_PROXY -u all_proxy -u https_proxy -u http_proxy
ENV := PYTHONPATH=src
ACCOUNTS ?= OpenAI,AnthropicAI

.PHONY: init setup wechat status test check x-cookies-init x-live-check

setup:
	$(PYTHON) -m venv $(VENV)
	$(NO_PROXY_ENV) $(PIP) install -r requirements.txt

wechat:
	$(NO_PROXY_ENV) $(ENV) $(PY) -m x_wechat_relay.wechat_bot run

status:
	$(NO_PROXY_ENV) $(ENV) $(PY) -m x_wechat_relay.wechat_bot status



x-cookies-init:
	$(ENV) $(PY) -m x_wechat_relay.x_source --init-cookies

x-live-check:
	$(NO_PROXY_ENV) $(ENV) $(PY) -m x_wechat_relay.x_source


test:
	$(ENV) $(PY) -m unittest discover -s tests

check:
	$(ENV) $(PY) -m compileall -q src tests
	$(ENV) $(PY) -m unittest discover -s tests
