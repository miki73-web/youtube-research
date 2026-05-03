import streamlit as st
import json
import threading
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_youtube():
    return build("youtube", "v3", developerKey=st.secrets["YOUTUBE_API_KEY"])


def get_sheets():
    info = json.loads(st.secrets["SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def search_videos(youtube, keyword, days, min_views, log):
    published_after = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    videos = []
    next_page_token = None
    max_pages = 3

    log("🔍 YouTubeを検索中...")

    for _ in range(max_pages):
        response = youtube.search().list(
            q=keyword,
            part="id,snippet",
            type="video",
            publishedAfter=published_after,
            order="viewCount",
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        video_ids = [item["id"]["videoId"] for item in response.get("items", [])]

        if video_ids:
            stats = youtube.videos().list(
                part="statistics,snippet",
                id=",".join(video_ids)
            ).execute()

            for item in stats.get("items", []):
                view_count = int(item["statistics"].get("viewCount", 0))
                if view_count >= min_views:
                    videos.append({
                        "title": item["snippet"]["title"],
                        "channel_id": item["snippet"]["channelId"],
                        "channel_title": item["snippet"]["channelTitle"],
                        "published_at": item["snippet"]["publishedAt"][:10],
                        "view_count": view_count,
                        "url": f"https://www.youtube.com/watch?v={item['id']}"
                    })

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    log(f"　再生数 {min_views:,} 回以上：**{len(videos)} 件**")
    return videos


def filter_by_subscribers(youtube, videos, max_subs, log):
    result = []
    channel_ids = list(set([v["channel_id"] for v in videos]))

    log("👥 登録者数を確認中...")

    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        response = youtube.channels().list(
            part="statistics",
            id=",".join(batch)
        ).execute()

        subscriber_map = {}
        for item in response.get("items", []):
            count = int(item["statistics"].get("subscriberCount", 0))
            subscriber_map[item["id"]] = count

        for video in videos:
            if video["channel_id"] in subscriber_map:
                sub_count = subscriber_map[video["channel_id"]]
                if sub_count <= max_subs:
                    video["subscriber_count"] = sub_count
                    result.append(video)

    log(f"　登録者 {max_subs:,} 人以下：**{len(result)} 件**")
    return result


def write_to_sheets(sheets, videos, keyword, spreadsheet_id, log):
    today = datetime.now().strftime("%Y-%m-%d")
    sheet_name = f"{today}_{keyword}"

    spreadsheet = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_names = [s["properties"]["title"] for s in spreadsheet["sheets"]]

    if sheet_name not in sheet_names:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
        ).execute()

    header = ["タイトル", "チャンネル名", "再生数", "登録者数", "投稿日", "URL"]
    rows = [header] + [
        [v["title"], v["channel_title"], v["view_count"], v["subscriber_count"], v["published_at"], v["url"]]
        for v in videos
    ]

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": rows}
    ).execute()

    log(f"✅ **{len(videos)} 件**をスプレッドシートに書き込みました！")
    log(f"📋 シート名：`{sheet_name}`")


# ─── UI ───────────────────────────────────────────

st.set_page_config(page_title="YouTube リサーチツール", page_icon="🎬", layout="centered")
st.title("🎬 YouTube リサーチツール")
st.caption("条件を入力して「リサーチ開始」を押してください")

# スプレッドシートID入力欄（フォームの外に出して説明を添える）
spreadsheet_id = st.text_input(
    "📊 スプレッドシートID",
    placeholder="例：1bmmWVlcDCkM1seH239xmwTTkHYo0jYNL-Yq-2ZrxJWI",
    help="スプレッドシートのURLの /d/ と /edit の間の文字列を貼り付けてください"
)

with st.expander("📌 スプレッドシートIDの確認方法と共有設定"):
    st.markdown("""
**① スプレッドシートIDの場所**

スプレッドシートのURLからコピーしてください：
```
https://docs.google.com/spreadsheets/d/【ここをコピー】/edit
```

**② 書き込み権限の設定（初回のみ）**

スプレッドシートを以下のメールアドレスと「編集者」で共有してください：
```
youtube-research@youtube-research-494812.iam.gserviceaccount.com
```
    """)

with st.form("search_form"):
    keyword   = st.text_input("🔑 キーワード", value="心理学")
    days      = st.number_input("📅 投稿期間（日以内）", min_value=1, max_value=3650, value=365)
    min_views = st.number_input("▶️ 最低再生数", min_value=0, value=10000, step=1000)
    max_subs  = st.number_input("👥 最大登録者数", min_value=0, value=4000, step=500)
    submitted = st.form_submit_button("🔍 リサーチ開始", use_container_width=True)

if submitted:
    if not keyword.strip():
        st.error("キーワードを入力してください")
    elif not spreadsheet_id.strip():
        st.error("スプレッドシートIDを入力してください")
    else:
        log_area = st.empty()
        logs = []

        def log(msg):
            logs.append(msg)
            log_area.markdown("\n\n".join(logs))

        try:
            youtube = get_youtube()
            sheets  = get_sheets()

            videos   = search_videos(youtube, keyword.strip(), int(days), int(min_views), log)
            filtered = filter_by_subscribers(youtube, videos, int(max_subs), log)

            if filtered:
                write_to_sheets(sheets, filtered, keyword.strip(), spreadsheet_id.strip(), log)
                st.success(f"完了！{len(filtered)} 件をスプレッドシートに出力しました 🎉")
            else:
                st.warning("条件に合う動画が見つかりませんでした。条件を変えて試してみてください。")

        except Exception as e:
            st.error(f"エラーが発生しました：{e}")
