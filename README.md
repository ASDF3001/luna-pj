# Luna

Discordサーバー向けのセキュリティ・モデレーションBot。
荒らし対策、認証システム、チケット管理などを一通り備えています。
discord.pyだけでいいのでMWS側でなんか入れないといけないっていうことはない

## 機能

- **Anti-Raid**: 短時間の大量参加を検知して処罰 (Kick / Ban / Timeout)
- **Anti-Spam**: メッセージ連投の検知・処罰
- **Anti-URL**: 過剰なURL投稿の検知
- **Anti-Nuke**: 短時間の大量チャンネル削除・Kick・Banを検知し、実行者を処罰
- **認証システム**: ボタンまたはリアクション式 + DM認証コード
- **チケット機能**: 個別サポート用のプライベートチャンネル作成
- **ロールパネル**: ボタン/セレクトメニューでロールを付け外し
- **メッセージ管理**: メッセージ一括削除 (Bot/Webhookのみのフィルタ対応)
- **チャンネル管理**: 確認付きのチャンネル削除
- **その他**: 埋め込み送信、ホワイト/ブラックリスト管理、ログ・トラップチャンネル通知
- 設定はすべてスラッシュコマンド対応

## 動作環境

- Python 3.11+
- discord.py 2.x
- SQLite3

## セットアップ & 起動

```bash
pip install discord.py

export DISCORD_BOT_TOKEN="your_bot_token"
# export ANTI_BOT_DB="anti_bot.sqlite3"  # DBパスを変更したい場合のみ

python bot/main.py
```

## ディレクトリ構成

```
bot/
├── main.py          # エントリーポイント
├── db.py            # DB接続・テーブル定義・ヘルパー
├── utils.py         # 共通ユーティリティ
└── cogs/
    ├── __init__.py  # 読み込みリスト
    ├── anti.py      # Anti-Raid / Spam / URL + Whitelist / Blacklist
    ├── auth.py      # 認証システム
    ├── ticket.py    # チケットシステム
    ├── general.py   # embed / hex / top / trap
    ├── update.py    # version / update
    ├── mod.py       # purge / channel_delete
    ├── nuke.py      # Anti-Nuke
    └── rolepanel.py # ロールパネル
todo/                # TODO管理
```

## License

Copyright (c) 2026 asdfyuto. All rights reserved.

This project is not open source. You may not copy, distribute, or modify this code without explicit permission.
