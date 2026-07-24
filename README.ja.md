# grill-me-codex

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

**2つの AI モデルで計画を鍛え上げ、その後は役割を入れ替えて構築します。** これは、AI 支援コーディングにおける2つの隔たりを埋める Claude Code スキル群です。1つは*あなたと Claude*の隔たり（何を構築するかについて合意できているか）、もう1つは*Claude とその成果物の品質*の隔たり（計画は本当に正しいのか、そしてそれをどう確かめるのか）です。

Act 1 では計画を確定するために**あなた**を徹底的に問い詰めます。Act 2 では、その計画を別プロバイダーの競合モデルである **OpenAI Codex** に渡し、両モデルが承認するまで数ラウンドにわたって対抗的に検証させます。Act 3（任意）では役割を反転し、**Codex が確定済みの計画からコードを書き**、**Claude がコントリビューターの PR と同じように差分をレビューします**。双方向のクロスモデルチェックにより、誰も自分自身の成果を採点しません。

> なぜ2つ目のモデルが必要なのでしょうか。同じモデルに構築を計画させ、実行させ、さらに*自分の仕事を評価させる*ことはできません。それではエコーチェンバーになってしまいます。別プロバイダーのモデルなら、Claude が自身の構造上見抜けない問題を発見できます。

[Matt Pocock の `grill-me` / `grill-with-docs`](https://github.com/mattpocock/skills) スキル（MIT）を基盤としています。Act 1 は彼の成果であり、反復的なクロスモデル Codex レビュー（Act 2）と役割を反転したビルド（Act 3）が本プロジェクトによる追加部分です。Act 3 の委任パターンは [Peter Steinberger の `codex-first`](https://github.com/steipete/agent-scripts) を基にしています。

## スキル

| スキル | Act 1 | Act 2 | Act 3 | 使用場面 |
|-------|-------|-------|-------|----------|
| **`grill-me-codex`** | 意思決定ツリーが解決するまで、Claude が一度に1つずつ質問します | Codex による対抗的レビューのループ | 任意 → `codex-build` | ゼロから計画し、認識合わせと2つ目のモデルによるチェックの両方が必要な場合 |
| **`grill-with-docs-codex`** | 同上。ただし、プロジェクトの `CONTEXT.md` 用語集に照らして計画を検証し、ADR をインラインで作成します | Codex レビューのループ | 任意 → `codex-build` | 同上。ただし、用語やアーキテクチャ上の決定が確立済みのプロジェクト |
| **`codex-review`** | —（計画は作成済み） | Codex レビューのループ | 任意 → `codex-build` | 計画がすでにあり、クロスモデルでストレステストだけを行いたい場合 |
| **`codex-build`** | — | — | Codex が確定済みの計画を実装し、Claude が検証します | レビュー済みの仕様があり、2つ目のモデルに実装させたい場合 |

## Act 2 の仕組み（レビュー）

1. Claude は確定した計画を `PLAN.md` に書き込み、`PLAN-REVIEW-LOG.md` にログを作成します。
2. **ラウンド1：** Codex は**読み取り専用サンドボックス**で計画をレビューし、`VERDICT: APPROVED` または `VERDICT: REVISE` を返します。
3. **ラウンド2..N：** Claude が修正し、*同じ* Codex セッションを再開します。これにより Codex は以前の指摘を記憶し、それらが解消されたかどうかだけを確認します。
4. `MAX_ROUNDS`（デフォルトは5）で上限を設定します。`APPROVED` を得るか上限に達すると終了します（偽の「承認」よりも、行き詰まりを明示する方が優れています）。
5. **あなたが判断するのは2回だけです。** 開始時と、コードを書く前の最終承認時です。Codex は全ラウンドで読み取り専用となり、ファイルには一切書き込みません。

成果物は2つです。`PLAN.md`（簡潔な最終計画、つまり*何をするか*）と `PLAN-REVIEW-LOG.md`（ラウンドごとの議論をすべて記録したもの、つまり*なぜそうするか*）です。

## Act 3 の仕組み（ビルド——役割の反転）

1. あなたが計画を承認すると、`codex-build` は `PLAN.md` を確定済みの仕様として Codex に渡します。Codex は**完全な書き込み権限**（`--yolo`）を取得し、テストを実行しながらエンドツーエンドで実装します。差分を分離して元に戻せるよう、開始前に **git ツリーがクリーンであること**が必要です。
2. 今度は批評役となった Claude が、コントリビューターの PR と同じように**差分全体**を読み、検証テストを自ら実行します。Codex の主張は参考情報にすぎず、Claude 自身による実行結果が証拠となります。
3. 問題は具体的な修正ラウンドとして*同じ* Codex セッションへ戻され、`MAX_FIX_ROUNDS`（デフォルトは2）で上限が設定されます。上限を超えると、モデル間でやり取りを続けるのではなく、Claude が引き継いで手作業で完了させます。
4. **あなたがもう一度判断します。** 差分を承認する段階です。コミットは Claude が作成し、Codex はコミットしません。
5. ビルドラウンドは同じ `PLAN-REVIEW-LOG.md` に追記されるため、問い詰め → レビュー → ビルド → 検証という全過程を1つの成果物で確認できます。

さらに、Codex セッションには**ネイティブの画像生成ツール**が用意されています（ChatGPT アカウントを利用し、API キーは不要です）。仕様には、「これらの画像アセットを自分で生成する」という手順を、正確なパスや寸法とともに含められます。スプライト、テクスチャ、背景などをビルド契約に組み込めます。

## インストール

スキルのフォルダーを Claude Code のスキルディレクトリへコピーします。

```bash
# macOS / Linux
cp -r skills/* ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\* $env:USERPROFILE\.claude\skills\
```

次に Claude Code で `/grill-me-codex`、`/grill-with-docs-codex`、`/codex-review`、または `/codex-build` を実行します。

## 前提条件

- **Codex CLI 0.130 以降** — `npm install -g @openai/codex@latest` を実行します（古いバージョンではデフォルトの `gpt-5.5` モデルでエラーになります）。
- **Codex の認証済みセッション** — `codex login` を一度実行します（ChatGPT アカウントを利用でき、Free/Plus/Pro/Max のすべてに対応します）。
- **モデルを固定しないこと** — ChatGPT アカウント認証では `gpt-5.x-codex` のモデルバリアントが拒否されます。スキルは設定上のデフォルトを使用します。

## 調整可能な項目

| スキル | 変数 | デフォルト | 意味 |
|-------|-----|---------|---------|
| レビュースキル | `MAX_ROUNDS` | `5` | レビューラウンド数の上限 |
| レビュースキル | `PLAN_FILE` | `PLAN.md` | 計画の保存先 |
| すべて | `LOG_FILE` | `PLAN-REVIEW-LOG.md` | 議論の記録 |
| `codex-build` | `SPEC_FILE` | `PLAN.md` | Codex が実装する確定済みの仕様 |
| `codex-build` | `MAX_FIX_ROUNDS` | `2` | Claude が引き継ぐまでの修正ラウンド数 |
| `codex-build` | `PROOF_CMD` | 仕様から取得 | 検証の根拠とする正確なテストコマンド |

実行時に `rounds=3` などを渡すと、設定を上書きできます。

## 安全性

**レビュースキル（Act 1〜2）：** Codex は**全ラウンドで読み取り専用**です。最初の呼び出しでは `-s read-only`、再開時には毎回 `-c sandbox_mode="read-only"` を使用します（`resume` サブコマンドは `-s` を受け付けず、読み取り専用を強制しなければ `config.toml` のサンドボックス既定値を継承します。この値は `danger-full-access` の場合があります）。これらの設定はスキルが処理します。最終計画を承認するまで、コードが書き込まれることはありません。

**`codex-build`（Act 3）**では意図的にこれを反転し、Codex に完全な書き込み権限を与えます。だからこそ厳格な判断ポイントが設けられています。開始前にクリーンなツリーが必要であり（差分の分離とクリーンな復元のため）、Claude は差分を1行ずつ読み、自ら検証を実行します。修正ラウンドには上限があり、コミットには人間の承認が必要で、Claude が作成します。再開時には長い形式のフラグ `--dangerously-bypass-approvals-and-sandbox` が必要です（resume には `--yolo` がありません）。また、必ず明示的な `thread_id` で再開し、`--last` は決して使用しないでください。ID が誤っていたり欠落していたりすると、別のセッションへ気付かないまま入ってしまう可能性があります。

## クレジット

- Act 1（`grill-me`、`grill-with-docs`）© Matt Pocock — https://github.com/mattpocock/skills（MIT）。各スキルの `THIRD-PARTY-NOTICES.md` を参照してください。
- Act 3 の Codex ビルダーパターンは Peter Steinberger の [`codex-first`](https://github.com/steipete/agent-scripts) を基にしています。
- Act 2（反復的な Codex レビュー）、Act 3（codex-build）、およびパッケージングは [Chase AI](https://youtube.com/@chaseai) によるものです。
- さらに深く学びたい場合は、**Claude Code Masterclass** と、エージェント型 AI を使ってプロダクトを開発するコミュニティを [Chase AI+](https://www.skool.com/chase-ai/about) でご覧ください。

## ライセンス

MIT — [LICENSE](./LICENSE) を参照してください。
