# X WeChat Relay

Forward updates from selected X accounts to your personal WeChat.

[中文](README.md)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![X](https://img.shields.io/badge/source-X-black)
![WeChat](https://img.shields.io/badge/notify-WeChat-07C160)

## Usage

```bash
make init ACCOUNTS="OpenAI,AnthropicAI,claudeai"
make wechat
```

On first run, paste your X cookies into `data/x_cookies.json`; after scanning the WeChat login QR code, send `bind` to the bot.

To watch different accounts, change `ACCOUNTS`.
