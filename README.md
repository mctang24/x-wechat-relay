# X WeChat Relay

将指定 X 账号的最新更新推送到个人微信。

[English](README_EN.md)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![X](https://img.shields.io/badge/source-X-black)
![WeChat](https://img.shields.io/badge/notify-WeChat-07C160)

## 使用

```bash
make init ACCOUNTS="OpenAI,AnthropicAI,claudeai"
make wechat
```

首次运行时，将 X cookies 填入 `data/x_cookies.json`；微信扫码登录后，向 bot 发送 `bind` 完成绑定。

要监听其他账号，只需要修改 `ACCOUNTS`。
