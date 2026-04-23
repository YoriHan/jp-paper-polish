#!/usr/bin/env python3
"""
jstage_scraper.py — 日本語学術表現コーパス収集スクリプト v2

J-STAGE から論文タイトルを収集し、内蔵シードコーパスと合わせて
高頻度の学術表現（接続表現・文末パターン・動詞フレーズ）を整理する。

使い方:
    python jstage_scraper.py
    python jstage_scraper.py --field "情報処理" --count 50 --out corpus.json

出力:
    jstage_corpus.json  — 表現リストをJSON形式で保存
    jstage_corpus.txt   — 人間が読みやすいテキスト形式

NOTE: J-STAGE APIはアブストラクトを返さないため、
      論文タイトルテキスト + 内蔵シードコーパスのハイブリッド方式を採用。
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
#  内蔵シードコーパス（実際の学術論文からの頻出表現）
# ──────────────────────────────────────────────

SEED_CONNECTIVES = [
    # 順接
    "そのため",
    "したがって",
    "このため",
    "これにより",
    "その結果",
    "それゆえ",
    "以上のことから",
    "これらのことから",
    # 逆接・対比
    "しかし",
    "一方",
    "これに対して",
    "一方で",
    "他方",
    # 付加・補足
    "また",
    "さらに",
    "加えて",
    "なお",
    "加えて",
    "併せて",
    # 言い換え
    "すなわち",
    "つまり",
    "換言すれば",
    # 導入
    "本研究では",
    "本稿では",
    "本論文では",
    "本章では",
    "本節では",
    # まとめ
    "以上のように",
    "以上を踏まえ",
    "以上の考察から",
    "今後の課題として",
    "今後の展望として",
]

SEED_SENTENCE_ENDS = [
    # 考察・判断
    "と考えられる",
    "と思われる",
    "と推察される",
    "と判断した",
    "と考える",
    # 示す・確認
    "が示された",
    "が確認された",
    "が明らかになった",
    "が示唆される",
    "が認められた",
    "が観察された",
    # 期待・可能性
    "が期待される",
    "と考えられる",
    "と見られる",
    "の可能性がある",
    "が予想される",
    # 目的・方法
    "を目的とする",
    "を目的とした",
    "を行った",
    "を実施した",
    "を検討した",
    "を試みた",
    "について検討した",
    "について考察した",
    "について述べる",
    # 提案・結論
    "を提案する",
    "を提案した",
    "を明らかにした",
    "を示した",
    "について報告する",
    # 評価
    "が有効であることが示された",
    "の有効性を確認した",
    "の有用性が示された",
]

SEED_VERB_PHRASES = [
    # 調査・分析
    "について検討した",
    "を分析した",
    "を調査した",
    "を評価した",
    "を比較した",
    "を検証した",
    "を考察した",
    "を観察した",
    "を測定した",
    # 提案・構築
    "を提案した",
    "を開発した",
    "を構築した",
    "を設計した",
    "を実装した",
    "を作成した",
    # 実験・検証
    "実験を行った",
    "調査を実施した",
    "分析を行った",
    "検討を行った",
    "比較検討を行った",
    # 示す・報告
    "結果を示した",
    "知見を報告する",
    "有効性を示した",
    "可能性を示した",
    # 着目・基づく
    "に着目した",
    "に基づいて",
    "を踏まえて",
    "に焦点を当てた",
]

# ──────────────────────────────────────────────
#  J-STAGE API からタイトル取得
# ──────────────────────────────────────────────

JSTAGE_API = "https://api.jstage.jst.go.jp/searchapi/do"

DEFAULT_QUERIES = [
    "機械学習",
    "自然言語処理",
    "情報処理",
    "環境工学",
    "社会科学",
    "経済政策",
]

TITLE_VERB_PATTERNS = [
    r"[検討提案分析評価比較確認検証調査考察開発構築設計実装](?:の|に関する|を用いた)",
    r"に関する研究",
    r"に関する考察",
    r"を用いた(?:研究|手法|方法|アプローチ)",
    r"に基づく",
    r"の(?:検討|提案|分析|評価|比較|考察|開発|構築)",
    r"について",
]


def fetch_titles(query: str, count: int = 20) -> list[str]:
    """J-STAGE から論文タイトルを取得する（アブストラクトの代替）"""
    params = urllib.parse.urlencode({
        "service": 3,
        "text": query,
        "lang": "ja",
        "count": min(count, 100),
    })
    url = f"{JSTAGE_API}?{params}"
    titles = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jp-paper-polish/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                t = title_el.text.strip()
                if len(t) > 5 and re.search(r'[\u3040-\u9fff]', t):
                    titles.append(t)
    except Exception as e:
        print(f"  [warn] {query}: {e}")
    return titles


def extract_from_titles(titles: list[str]) -> dict[str, list[str]]:
    """論文タイトルから学術表現を抽出する"""
    verb_hits = []
    for title in titles:
        for pat in TITLE_VERB_PATTERNS:
            for m in re.finditer(pat, title):
                context = title[max(0, m.start()-6):m.end()+4].strip()
                if len(context) > 4:
                    verb_hits.append(context)
    return {"verbs_from_titles": verb_hits}


# ──────────────────────────────────────────────
#  コーパス構築
# ──────────────────────────────────────────────

def build_corpus(queries: list[str], count_per_query: int = 20) -> dict:
    all_titles = []
    print(f"Fetching titles from J-STAGE ({len(queries)} queries × {count_per_query} papers)...")
    for q in queries:
        titles = fetch_titles(q, count_per_query)
        print(f"  {q}: {len(titles)} titles")
        all_titles.extend(titles)
    print(f"Total titles collected: {len(all_titles)}\n")

    # タイトルから動詞フレーズを抽出
    from_titles = extract_from_titles(all_titles)
    title_verb_counter = Counter(from_titles["verbs_from_titles"])

    # シードコーパスをカウント形式に変換（全て出現数 1 として登録）
    connective_counter = Counter({c: 1 for c in SEED_CONNECTIVES})
    sentence_end_counter = Counter({s: 1 for s in SEED_SENTENCE_ENDS})
    verb_counter = Counter({v: 1 for v in SEED_VERB_PHRASES})

    # タイトル由来の動詞フレーズを追加
    verb_counter.update(title_verb_counter)

    corpus = {
        "connectives": dict(connective_counter.most_common(50)),
        "sentence_ends": dict(sentence_end_counter.most_common(50)),
        "verbs": dict(verb_counter.most_common(50)),
        "meta": {
            "queries": queries,
            "titles_collected": len(all_titles),
            "seed_connectives": len(SEED_CONNECTIVES),
            "seed_sentence_ends": len(SEED_SENTENCE_ENDS),
            "seed_verbs": len(SEED_VERB_PHRASES),
            "note": "Corpus = curated seed expressions + J-STAGE title-derived patterns. "
                    "J-STAGE API does not expose abstracts in search endpoint."
        }
    }
    return corpus


# ──────────────────────────────────────────────
#  出力
# ──────────────────────────────────────────────

def save_corpus(corpus: dict, out_json: str = "jstage_corpus.json"):
    out_txt = out_json.replace(".json", ".txt")

    Path(out_json).write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved JSON → {out_json}")

    lines = [
        "# 日本語学術表現コーパス",
        f"# J-STAGE タイトル収集数: {corpus['meta']['titles_collected']}",
        "",
        "## 接続表現（文頭・節頭）",
    ]
    for expr, cnt in corpus["connectives"].items():
        lines.append(f"  {expr}")
    lines += ["", "## 文末パターン"]
    for expr, cnt in corpus["sentence_ends"].items():
        lines.append(f"  〜{expr}。")
    lines += ["", "## 動詞フレーズ"]
    for expr, cnt in corpus["verbs"].items():
        marker = " ★" if cnt > 1 else ""
        lines.append(f"  〜{expr}{marker}")
    lines += ["", "★ = J-STAGEタイトルでも高頻度"]

    Path(out_txt).write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved TXT  → {out_txt}")


def main():
    parser = argparse.ArgumentParser(description="日本語学術表現コーパス収集")
    parser.add_argument("--field", type=str, default="", help="追加検索キーワード")
    parser.add_argument("--count", type=int, default=20, help="クエリあたりの取得件数")
    parser.add_argument("--out", type=str, default="jstage_corpus.json")
    args = parser.parse_args()

    queries = DEFAULT_QUERIES.copy()
    if args.field:
        queries.append(args.field)

    corpus = build_corpus(queries, count_per_query=args.count)
    save_corpus(corpus, out_json=args.out)

    print("\n── Top 接続表現 ──")
    for k in list(corpus["connectives"].keys())[:10]:
        print(f"  {k}")
    print("\n── Top 文末パターン ──")
    for k in list(corpus["sentence_ends"].keys())[:10]:
        print(f"  〜{k}。")
    print("\n── Top 動詞フレーズ（J-STAGEタイトル由来含む）──")
    for k, v in list(corpus["verbs"].items())[:10]:
        mark = " ★" if v > 1 else ""
        print(f"  〜{k}{mark}")


if __name__ == "__main__":
    main()
