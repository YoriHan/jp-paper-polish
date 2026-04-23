#!/usr/bin/env python3
"""
jstage_scraper.py — J-STAGE 学術表現コーパス収集スクリプト

指定した分野のキーワードで J-STAGE を検索し、論文アブストラクトから
高頻度の学術表現（接続表現・文末パターン・動詞の形）を抽出する。

使い方:
    python jstage_scraper.py
    python jstage_scraper.py --field "情報処理" --count 50 --out corpus.json

出力:
    JSON に以下の3カテゴリを保存:
    - connectives   : 接続表現（そのため、したがって、一方で、など）
    - sentence_ends : 文末パターン（〜と考えられる、〜を示した、など）
    - verbs         : 論文頻出動詞フレーズ（検討した、分析した、など）
    テキストファイルにも人間が読める形式で保存する。
"""

import argparse
import json
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


# ──────────────────────────────────────────────
#  J-STAGE API
# ──────────────────────────────────────────────

JSTAGE_API = "https://api.jstage.jst.go.jp/searchapi/do"

# 検索する分野キーワード（デフォルト）
DEFAULT_QUERIES = [
    "機械学習",
    "自然言語処理",
    "情報処理",
    "環境工学",
    "社会学",
    "経済政策",
]


def fetch_abstracts(query: str, count: int = 20) -> list[str]:
    """J-STAGE から指定キーワードのアブストラクトを取得する"""
    params = urllib.parse.urlencode({
        "service": 3,
        "text": query,
        "lang": "ja",
        "count": count,
    })
    url = f"{JSTAGE_API}?{params}"
    abstracts = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jp-paper-polish/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            summary = entry.find("atom:summary", ns)
            if summary is not None and summary.text:
                text = summary.text.strip()
                if len(text) > 50:
                    abstracts.append(text)
    except Exception as e:
        print(f"  [warn] {query}: {e}")
    return abstracts


# ──────────────────────────────────────────────
#  表現抽出パターン
# ──────────────────────────────────────────────

# 接続表現（文頭・節頭に来る表現）
CONNECTIVE_PATTERNS = [
    r"そのため[、，]",
    r"したがって[、，]",
    r"一方[、，（(]",
    r"また[、，]",
    r"さらに[、，]",
    r"なお[、，]",
    r"しかし[、，]",
    r"これに対して[、，]",
    r"これにより[、，]",
    r"その結果[、，]",
    r"本研究では[、，]",
    r"本稿では[、，]",
    r"本論文では[、，]",
    r"以上のことから[、，]",
    r"以上を踏まえ[てた]",
    r"これらのことから[、，]",
    r"今後の課題として[は]",
]

# 文末パターン（。の直前）
SENTENCE_END_PATTERNS = [
    r"[^。]{5,40}と考えられる。",
    r"[^。]{5,40}が示された。",
    r"[^。]{5,40}が確認された。",
    r"[^。]{5,40}が明らかになった。",
    r"[^。]{5,40}が示唆される。",
    r"[^。]{5,40}と思われる。",
    r"[^。]{5,40}が期待される。",
    r"[^。]{5,40}を目的とする。",
    r"[^。]{5,40}について検討した。",
    r"[^。]{5,40}を分析した。",
    r"[^。]{5,40}を提案する。",
    r"[^。]{5,40}を行った。",
    r"[^。]{5,40}を明らかにした。",
    r"[^。]{5,40}とした。",
]

# 頻出動詞フレーズ（抽象化して末尾部分だけ）
VERB_PHRASE_PATTERNS = [
    r"[検討提案分析評価比較確認検証調査考察]し[たて]",
    r"[検討提案分析評価比較確認検証調査考察]する",
    r"[検討提案分析評価比較確認検証調査考察]した",
    r"を行[ったい]",
    r"を示[したす]",
    r"に着目し[たて]",
    r"に基づ[いく]",
    r"について述べ[たる]",
    r"について論じ[たる]",
    r"に関して[は]",
]


def extract_connectives(text: str) -> list[str]:
    hits = []
    for pat in CONNECTIVE_PATTERNS:
        for m in re.finditer(pat, text):
            hits.append(m.group().rstrip("、，"))
    return hits


def extract_sentence_ends(text: str) -> list[str]:
    hits = []
    for pat in SENTENCE_END_PATTERNS:
        for m in re.finditer(pat, text):
            # 末尾の動詞フレーズ部分だけ取り出す（最後の8〜15文字）
            s = m.group()
            tail = s[-15:].lstrip("はがをにのでも")
            hits.append(tail.strip())
    return hits


def extract_verb_phrases(text: str) -> list[str]:
    hits = []
    for pat in VERB_PHRASE_PATTERNS:
        for m in re.finditer(pat, text):
            hits.append(m.group())
    return hits


# ──────────────────────────────────────────────
#  メイン
# ──────────────────────────────────────────────

def build_corpus(queries: list[str], count_per_query: int = 20) -> dict:
    all_abstracts = []
    print(f"Fetching from J-STAGE ({len(queries)} queries × {count_per_query} papers)...")
    for q in queries:
        abs_list = fetch_abstracts(q, count_per_query)
        print(f"  {q}: {len(abs_list)} abstracts")
        all_abstracts.extend(abs_list)

    print(f"\nTotal abstracts collected: {len(all_abstracts)}")

    connective_counter: Counter = Counter()
    sentence_end_counter: Counter = Counter()
    verb_counter: Counter = Counter()

    for text in all_abstracts:
        for c in extract_connectives(text):
            connective_counter[c] += 1
        for se in extract_sentence_ends(text):
            sentence_end_counter[se] += 1
        for vp in extract_verb_phrases(text):
            verb_counter[vp] += 1

    corpus = {
        "connectives": dict(connective_counter.most_common(40)),
        "sentence_ends": dict(sentence_end_counter.most_common(40)),
        "verbs": dict(verb_counter.most_common(40)),
        "meta": {
            "queries": queries,
            "abstracts_collected": len(all_abstracts),
        }
    }
    return corpus


def save_corpus(corpus: dict, out_json: str = "jstage_corpus.json", out_txt: str = "jstage_corpus.txt"):
    # JSON
    Path(out_json).write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved JSON → {out_json}")

    # 人間が読みやすいテキスト
    lines = ["# J-STAGE 学術表現コーパス", ""]

    lines.append("## 接続表現（connectives）")
    for expr, cnt in corpus["connectives"].items():
        lines.append(f"  {expr}　({cnt}件)")
    lines.append("")

    lines.append("## 文末パターン（sentence endings）")
    for expr, cnt in corpus["sentence_ends"].items():
        lines.append(f"  〜{expr}　({cnt}件)")
    lines.append("")

    lines.append("## 動詞フレーズ（verb phrases）")
    for expr, cnt in corpus["verbs"].items():
        lines.append(f"  〜{expr}　({cnt}件)")
    lines.append("")

    lines.append(f"## メタ情報")
    lines.append(f"  収集アブストラクト数: {corpus['meta']['abstracts_collected']}")
    lines.append(f"  検索クエリ: {', '.join(corpus['meta']['queries'])}")

    Path(out_txt).write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved TXT  → {out_txt}")


def main():
    parser = argparse.ArgumentParser(description="J-STAGE 学術表現コーパス収集")
    parser.add_argument("--field", type=str, default="", help="追加する検索キーワード")
    parser.add_argument("--count", type=int, default=20, help="クエリあたりの取得件数 (max 100)")
    parser.add_argument("--out", type=str, default="jstage_corpus.json", help="出力JSONファイル名")
    args = parser.parse_args()

    queries = DEFAULT_QUERIES.copy()
    if args.field:
        queries.append(args.field)

    corpus = build_corpus(queries, count_per_query=args.count)
    save_corpus(corpus, out_json=args.out, out_txt=args.out.replace(".json", ".txt"))

    # サマリ表示
    print("\n── Top 10 接続表現 ──")
    for k, v in list(corpus["connectives"].items())[:10]:
        print(f"  {k} ({v})")
    print("\n── Top 10 文末パターン ──")
    for k, v in list(corpus["sentence_ends"].items())[:10]:
        print(f"  〜{k} ({v})")
    print("\n── Top 10 動詞フレーズ ──")
    for k, v in list(corpus["verbs"].items())[:10]:
        print(f"  〜{k} ({v})")


if __name__ == "__main__":
    main()
