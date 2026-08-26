from pathlib import Path

import pandas as pd


SOURCE_FILE = Path("steam_top100_all.csv")
OUTPUT_DIR = Path("data/processed/trend")
REGIONS = ["CN", "JP", "US", "GLOBAL"]

OUTPUT_COLUMNS = [
    "week_start",
    "country",
    "appid",
    "title",
    "week_start_date",
    "rank",
    "is_ranked",
]


def normalize_appid(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def make_game_key(row):
    appid = row["appid"]
    title = row["title"]
    if appid in {"", "0", "nan", "None", "<NA>"}:
        return f"TITLE::{title}"
    return f"APP::{appid}"


def build_region_trend(source_df, region, all_weeks):
    region_df = source_df[source_df["country"] == region].copy()

    region_df["appid"] = region_df["appid"].map(normalize_appid)
    region_df["game_key"] = region_df.apply(make_game_key, axis=1)

    # For rare placeholder rows such as unavailable titles, keep the best rank
    # if Steam exposes multiple indistinguishable rows in the same week.
    actual = (
        region_df.sort_values(["week_start", "rank"])
        .drop_duplicates(["week_start", "game_key"], keep="first")
        [["week_start", "game_key", "rank"]]
    )

    latest_meta = (
        region_df.sort_values("week_start")
        .drop_duplicates("game_key", keep="last")
        [["game_key", "appid", "title"]]
    )

    games = latest_meta.copy()
    games["join_key"] = 1

    weeks = pd.DataFrame({"week_start": all_weeks})
    weeks["join_key"] = 1

    trend = games.merge(weeks, on="join_key", how="outer").drop(columns="join_key")
    trend = trend.merge(actual, on=["week_start", "game_key"], how="left")

    trend["country"] = region
    trend["week_start_date"] = trend["week_start"]
    trend["rank"] = trend["rank"].astype("Int64")
    trend["is_ranked"] = trend["rank"].notna().astype(int)

    trend = trend[
        [
            "week_start",
            "country",
            "appid",
            "title",
            "week_start_date",
            "rank",
            "is_ranked",
        ]
    ].sort_values(["country", "appid", "title", "week_start"])

    trend["week_start"] = trend["week_start"].dt.strftime("%Y-%m-%d")
    trend["week_start_date"] = trend["week_start_date"].dt.strftime("%Y-%m-%d")

    return trend


def main():
    if not SOURCE_FILE.exists():
        raise FileNotFoundError(f"Missing source file: {SOURCE_FILE}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(SOURCE_FILE, dtype={"appid": "string"}, low_memory=False)

    required = {"week_start", "country", "appid", "title", "rank"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing required columns: {sorted(missing)}")

    df["week_start"] = pd.to_datetime(df["week_start"], errors="raise")
    df["country"] = df["country"].astype(str).str.upper().str.strip()
    df["rank"] = pd.to_numeric(df["rank"], errors="raise").astype(int)

    all_weeks = sorted(df["week_start"].drop_duplicates().tolist())

    print(f"Source rows: {len(df)}")
    print(f"Weeks: {len(all_weeks)}")
    print(f"First week: {all_weeks[0].date()}")
    print(f"Last week: {all_weeks[-1].date()}")

    for region in REGIONS:
        trend = build_region_trend(df, region, all_weeks)
        output_file = OUTPUT_DIR / f"steam_top100_trend_blank_{region}.csv"
        trend.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"{region}: {len(trend)} rows -> {output_file}")


if __name__ == "__main__":
    main()
