import os
import json
import time
import random
import hashlib
import urllib.parse

HAS_NETEASE = False
try:
    from NeteaseCloudMusic import NeteaseCloudMusicApi
    HAS_NETEASE = True
except ImportError:
    pass

# pycryptodome（用于在 Python 端实现 eapi 加密，规避 JS 加密对中文的破坏）
HAS_CRYPTO = False
try:
    from Crypto.Cipher import AES as _AES
    HAS_CRYPTO = True
except ImportError:
    pass

# 网易云 eapi 固定加密 key
_EAPI_KEY = b"e82ckenh8dichen8"

# ---- 一些常见的排行榜 ID ----
TOPLIST_IDS = {
    "云音乐飙升榜": 19723756,
    "云音乐热歌榜": 3779629,
    "网易原创歌曲榜": 2884035,
    "云音乐说唱榜": 991319590,
    "云音乐古典榜": 71384707,
    "云音乐电音榜": 1978921795,
    "华语金曲榜": 64016,
    "欧美金曲榜": 112504,
    "日语榜": 11641012,
    "韩国榜": 745956077,
    "Beatport全球电子舞曲榜": 3812895,
}

class NeteaseAPI:
    def __init__(self, cache_dir=None):
        if not HAS_NETEASE:
            raise ImportError("NeteaseCloudMusic 未安装")
        self._api = NeteaseCloudMusicApi()
        # NeteaseCloudMusicApi.js 引用了全局变量 anonymous_token（原 Node 版由
        # 服务端注入，Python 移植包缺失），不定义则所有走 JS 加密链路的接口
        # （如 /song/url/v1）报 ReferenceError。此处注入原项目的默认匿名 token。
        self._api.ctx.eval(
            'var anonymous_token = "aaa5bb2592398e884be23fabbb966bf4";')
        self.cache_dir = cache_dir

    # ---------- 搜索 ----------
    def search(self, keywords, limit=30, page=1, mode="song"):
        """按字段搜索歌曲，均返回歌曲列表。

        mode:
          - "song"   （默认）按歌名/综合搜索 → /cloudsearch type=1
          - "artist" 按歌手名搜索 → 先搜歌手(type=100)再取该歌手热门歌曲
          - "album"  按专辑名搜索 → 先搜专辑(type=10)再取该专辑的歌曲

        NeteaseCloudMusicApi 的 JS 加密（crypto-js）在 MiniRacer 环境下会把中文
        关键词破坏成乱码（如“晴天”→“f74Y29”），导致中文搜索结果与关键词无关。
        因此优先在 Python 端用标准 AES 实现 eapi 加密（中文无损）：
          1. Python 端 eapi 加密 → /cloudsearch（最完整，含封面）
          2. 失败则回退明文 web 搜索接口（支持中文，无封面）
          3. 再失败则回退 JS 加密接口（英文场景可用）
        """
        if mode == "artist":
            return self._search_artist_songs(keywords, limit)
        if mode == "album":
            return self._search_album_songs(keywords, limit)
        # 歌名（默认）：综合回退链路
        return self._search_song_fallback(keywords, limit, page)

    def _eapi_request(self, api_name, params, ip=""):
        """在 Python 端实现网易云 eapi 加密请求，正确处理中文参数。

        复刻 NeteaseCloudMusicApi.js 的 request_param / eapi 加密逻辑：
        eapi 明文 = f"{url}-36cd479b6b5-{text}-36cd479b6b5-{md5digest}"
        AES-128-ECB + PKCS7，key 为 e82ckenh8dichen8。
        服务端对 eapi 返回明文 JSON，直接解析即可。
        """
        url = f"/api/{api_name.lstrip('/')}"
        # 游客 cookie（与 JS 端 createRequestParam 行为一致）
        cookie = {
            "__remember_me": True,
            "_ntes_nuid": f"{random.getrandbits(128):032x}",
            "NMTID": f"{random.getrandbits(128):032x}",
            "MUSIC_A": "",
            "os": "ios",
            "appver": "8.10.90",
        }
        header = {
            "appver": cookie.get("appver") or "8.9.70",
            "versioncode": cookie.get("versioncode") or "140",
            "buildver": cookie.get("buildver") or str(int(time.time() * 1000))[:10],
            "resolution": cookie.get("resolution") or "1920x1080",
            "__csrf": "",
            "os": cookie.get("os") or "android",
            "requestId": f"{int(time.time()*1000)}_{random.randint(0, 9999):04d}",
        }
        payload = dict(params)
        payload["header"] = header
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        message = f"nobody{url}use{text}md5forencrypt"
        digest = hashlib.md5(message.encode("utf-8")).hexdigest()
        plain = f"{url}-36cd479b6b5-{text}-36cd479b6b5-{digest}"
        params_hex = self._aes_ecb_encrypt(plain)

        req_headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/116.0.0.0 Safari/537.36"),
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://music.163.com",
            "Cookie": "; ".join(
                f"{urllib.parse.quote(str(k))}={urllib.parse.quote(str(v))}"
                for k, v in cookie.items()),
        }
        if ip:
            req_headers["X-Real-IP"] = ip
            req_headers["X-Forwarded-For"] = ip

        import requests
        resp = requests.post(
            "https://interface.music.163.com" + url.replace("/api/", "/eapi/"),
            data={"params": params_hex}, headers=req_headers, timeout=20)
        return resp.json()

    @staticmethod
    def _aes_ecb_encrypt(text):
        """AES-128-ECB + PKCS7 加密，返回大写 hex"""
        data = text.encode("utf-8")
        pad = 16 - len(data) % 16
        data += bytes([pad]) * pad
        return _AES.new(_EAPI_KEY, _AES.MODE_ECB).encrypt(data).hex().upper()

    @staticmethod
    def _split_artist_names(keywords):
        """把多歌手关键词拆分为歌手名列表。

        歌手列数据用 ", " 连接（如 "周杰伦, 费玉清"），用户也可能输入
        "、"、"&"、"+" 等分隔（注意："/" 与 "和" 不作为分隔符，因为
        部分歌手/组合名本身含这些字符）。拆分规则：
          1. 先用强分隔符（,，、&＋+）拆分；
          2. 仅当不存在强分隔符时按空格拆分，且要求整串含中文，
             避免把含空格的英文歌手名（如 "Taylor Swift"）拆坏。
        """
        import re
        kw = (keywords or "").strip()
        if not kw:
            return []
        parts = [p.strip() for p in re.split(r"[、,&，＋+]", kw) if p.strip()]
        # 无强分隔符 → 尝试按空格拆分（仅限含中文的串）
        if len(parts) == 1 and re.search(r"[\u4e00-\u9fff]", kw):
            space_parts = [p.strip() for p in re.split(r"\s+", kw) if p.strip()]
            if len(space_parts) > 1:
                parts = space_parts
        # 去重（保序）
        return list(dict.fromkeys(parts))

    def _search_artist_songs(self, keywords, limit=30):
        """按歌手名搜索。

        单歌手：搜歌手(type=100)取歌手ID → 热门歌曲；
        多歌手（如 "周杰伦, 费玉清"）：拆分为多个歌手分别搜索热门歌曲，
        合并去重，合作曲优先排序；任一步失败回退歌曲搜索。
        注意：多歌手关键词直接按整体搜 type=100 时，artists[0] 可能是不相关的
        歌手，因此必须拆分后再逐个精确匹配。
        """
        names = self._split_artist_names(keywords)
        if len(names) > 1:
            songs, err = self._search_multi_artists(names, limit)
            if err is None:
                return songs, None
            # 拆分搜索全部失败 → 回退歌曲搜索
            return self._search_song_fallback(keywords, limit, 1)
        # 单歌手
        name = names[0] if names else keywords
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/cloudsearch/pc", {
                    "s": name, "type": 100, "limit": 5,
                    "offset": 0, "total": True,
                })
                if raw and raw.get("code") == 200:
                    artists = ((raw.get("result") or {}).get("artists") or [])
                    if artists:
                        # 优先精确匹配歌手名，避免取到名称变体（如 "周杰伦."）
                        artist = next(
                            (a for a in artists if (a.get("name") or "").strip() == name),
                            artists[0])
                        raw2 = self._eapi_request(
                            "/v1/artist/top/song", {"id": artist["id"]})
                        if raw2 and raw2.get("code") == 200:
                            songs_raw = raw2.get("songs", []) or []
                            if songs_raw:
                                songs = [self._build_song_item(s)
                                         for s in songs_raw[:limit]]
                                print(f"✅ 按歌手搜索「{name}」：{len(songs)} 首")
                                return songs, None
            except Exception as e:
                print(f"⚠️ 按歌手搜索失败，回退：{e}")
        # 回退：普通歌曲搜索（网易云歌曲搜索会匹配歌手字段）
        return self._search_song_fallback(keywords, limit, 1)

    def _search_multi_artists(self, names, limit=30):
        """多歌手：逐个歌手搜索热门歌曲，合并去重，合作曲优先。"""
        if not HAS_CRYPTO:
            return None, "需要加密支持"
        per = max(5, min(20, limit))  # 每个歌手的配额，避免单个歌手占满结果
        all_songs = []
        seen = set()
        failures = []
        for name in names:
            try:
                raw = self._eapi_request("/cloudsearch/pc", {
                    "s": name, "type": 100, "limit": 5,
                    "offset": 0, "total": True,
                })
                if not raw or raw.get("code") != 200:
                    failures.append(name)
                    continue
                artists = ((raw.get("result") or {}).get("artists") or [])
                artist = next(
                    (a for a in artists if (a.get("name") or "").strip() == name),
                    None)
                if artist is None:
                    artist = artists[0] if artists else None
                if artist is None:
                    failures.append(name)
                    continue
                raw2 = self._eapi_request(
                    "/v1/artist/top/song", {"id": artist["id"]})
                if not raw2 or raw2.get("code") != 200:
                    failures.append(name)
                    continue
                for s in (raw2.get("songs", []) or [])[:per]:
                    item = self._build_song_item(s)
                    if item["song_id"] not in seen:
                        seen.add(item["song_id"])
                        all_songs.append(item)
            except Exception:
                failures.append(name)
        if not all_songs:
            return None, f"未找到歌手「{'、'.join(names)}」"
        if failures:
            print(f"⚠️ 部分歌手未找到：{'、'.join(failures)}")
        # 合作曲优先：歌曲歌手字段命中目标歌手越多排越前
        def hit_count(item):
            singer = item.get("singer", "") or ""
            return sum(1 for n in names if n and n in singer)
        all_songs.sort(key=lambda it: -hit_count(it))
        print(f"✅ 按歌手搜索「{'、'.join(names)}」：{len(all_songs)} 首")
        return all_songs[:limit], None

    def _search_album_songs(self, keywords, limit=30):
        """按专辑名搜索：先搜专辑(type=10)取专辑ID，再取该专辑的歌曲"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/cloudsearch/pc", {
                    "s": keywords, "type": 10, "limit": 5,
                    "offset": 0, "total": True,
                })
                if raw and raw.get("code") == 200:
                    albums = ((raw.get("result") or {}).get("albums") or [])
                    if not albums:
                        return [], f"未找到专辑「{keywords}」"
                    album_id = albums[0]["id"]
                    raw2 = self._eapi_request(f"/v1/album/{album_id}", {})
                    if raw2 and raw2.get("code") == 200:
                        songs_raw = raw2.get("songs", []) or []
                        if not songs_raw:
                            return [], f"专辑「{keywords}」暂无歌曲"
                        songs = [self._build_song_item(s) for s in songs_raw[:limit]]
                        print(f"✅ 按专辑搜索「{keywords}」：{len(songs)} 首")
                        return songs, None
            except Exception as e:
                print(f"⚠️ 按专辑搜索失败，回退：{e}")
        # 回退：普通歌曲搜索（网易云歌曲搜索会匹配专辑字段）
        return self._search_song_fallback(keywords, limit, 1)

    def _search_song_fallback(self, keywords, limit=30, page=1):
        """歌名/综合模式的回退链路（Python eapi → web 明文 → JS 接口）"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/cloudsearch/pc", {
                    "s": keywords, "type": 1,
                    "limit": limit, "offset": (page - 1) * limit,
                    "total": True,
                })
                if raw and raw.get("code") == 200:
                    return self._parse_search_result({"body": raw})
            except Exception as e:
                print(f"⚠️ Python eapi 搜索失败，回退：{e}")
        songs, err = self._search_web(keywords, limit, page)
        if err is None:
            return songs, None
        try:
            result = self._api.request("/cloudsearch", {
                "keywords": keywords, "type": 1,
                "limit": limit, "offset": (page - 1) * limit,
            })
            return self._parse_search_result(result)
        except Exception as e:
            return songs, err or f"搜索失败：{e}"

    def _search_web(self, keywords, limit=30, page=1):
        """使用网易云明文 web 搜索接口（支持中文，但无封面字段）"""
        try:
            import requests
            url = "https://music.163.com/api/search/get/web"
            params = {"s": keywords, "type": 1,
                      "limit": limit, "offset": (page - 1) * limit}
            headers = {
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/116.0.0.0 Safari/537.36"),
                "Referer": "https://music.163.com",
            }
            r = requests.get(url, params=params, headers=headers, timeout=20)
            data = r.json()
            if data.get("code") != 200:
                return [], f"搜索失败 (code={data.get('code')})"
            song_list = (data.get("result") or {}).get("songs") or []
            songs = []
            for s in song_list:
                singers = ", ".join(a.get("name", "") for a in s.get("artists", []))
                duration_sec = s.get("duration", 0) // 1000
                songs.append({
                    "song_id": s["id"],
                    "name": s.get("name", ""),
                    "singer": singers,
                    "album": (s.get("album") or {}).get("name", ""),
                    "cover_url": "",
                    "duration": self._fmt_duration(duration_sec),
                    "duration_sec": duration_sec,
                    "filepath": None,
                })
            return songs, None
        except Exception as e:
            return [], f"明文搜索接口失败：{e}"

    def _parse_search_result(self, raw):
        songs = []
        try:
            data = raw.get("body", raw) if isinstance(raw, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                print(f"⚠️ 搜索 API 返回 code={code}")
                return songs, f"搜索失败 (code={code})"
            # 兼容两种响应结构：JS 库包装的 data.result 与直接响应的 result
            inner = data.get("data", {})
            result = inner.get("result", {}) if isinstance(inner, dict) else {}
            if not result:
                result = data.get("result", {})
            song_list = result.get("songs", [])
            if not song_list:
                print("⚠️ 搜索无结果（songs 为空）")
            for s in song_list:
                singers = ", ".join(a["name"] for a in s.get("ar", []))
                duration_sec = s.get("dt", 0) // 1000
                songs.append({
                    "song_id": s["id"],
                    "name": s["name"],
                    "singer": singers,
                    "album": s.get("al", {}).get("name", ""),
                    "cover_url": s.get("al", {}).get("picUrl", ""),
                    "duration": self._fmt_duration(duration_sec),
                    "duration_sec": duration_sec,
                    "filepath": None,
                })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return songs, f"解析搜索结果失败：{e}"
        return songs, None

    @staticmethod
    def _build_song_item(s):
        """把 /song/detail、/playlist/detail、/simiSong 等返回的歌曲对象解析为统一结构。

        兼容新版字段（ar/al/dt）与老版字段（artists/album/duration）。
        """
        ar = s.get("ar") or s.get("artists") or []
        album = s.get("al") or s.get("album") or {}
        singers = ", ".join(a.get("name", "") for a in ar)
        duration_sec = (s.get("dt") or s.get("duration") or 0) // 1000
        return {
            "song_id": s["id"],
            "name": s.get("name", ""),
            "singer": singers,
            "album": album.get("name", ""),
            "cover_url": album.get("picUrl", ""),
            "duration": NeteaseAPI._fmt_duration(duration_sec),
            "duration_sec": duration_sec,
            "filepath": None,
        }

    # ---------- 排行榜 ----------
    def get_toplist_list(self):
        """获取所有排行榜列表"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/toplist", {})
                if raw and raw.get("code") == 200:
                    raw_list = raw.get("list", [])
                    toplists = [{
                        "id": item["id"],
                        "name": item["name"],
                        "cover": item.get("coverImgUrl", ""),
                        "update_freq": item.get("updateFrequency", ""),
                        "song_count": item.get("trackCount", 0),
                    } for item in raw_list]
                    return toplists, None
            except Exception as e:
                print(f"⚠️ Python eapi 获取排行榜失败，回退：{e}")
        result = self._api.request("/toplist")
        try:
            data = result.get("body", result) if isinstance(result, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                return [], f"获取排行榜失败 (code={code})"
            # 兼容两种结构：JS 库包装的 data.list 与直接响应的顶层 list
            inner = data.get("data", {})
            raw_list = inner.get("list", []) if isinstance(inner, dict) else []
            if not raw_list:
                raw_list = data.get("list", [])
            toplists = []
            for item in raw_list:
                toplists.append({
                    "id": item["id"],
                    "name": item["name"],
                    "cover": item.get("coverImgUrl", ""),
                    "update_freq": item.get("updateFrequency", ""),
                    "song_count": item.get("trackCount", 0),
                })
            return toplists, None
        except Exception as e:
            return [], f"解析排行榜列表失败：{e}"

    def get_toplist_songs(self, toplist_id):
        """获取指定排行榜的歌曲列表"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/v6/playlist/detail",
                                         {"id": toplist_id, "n": 100000, "s": 8})
                if raw and raw.get("code") == 200:
                    playlist = raw.get("playlist", {}) or {}
                    tracks_raw = playlist.get("tracks", []) or []
                    songs = [self._build_song_item(s) for s in tracks_raw]
                    print(f"✅ 排行榜获取成功：{len(songs)} 首歌曲")
                    return songs, None
            except Exception as e:
                print(f"⚠️ Python eapi 获取排行榜详情失败，回退：{e}")
        result = self._api.request("/playlist/detail", {"id": toplist_id})
        try:
            data = result.get("body", result) if isinstance(result, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                return [], f"获取排行榜详情失败 (code={code})"
            # playlist_detail 返回嵌套在 data 里
            inner = data.get("data", {})
            playlist = inner.get("playlist", {})
            if not playlist:
                print(f"⚠️ playlist_detail 无 playlist 数据")
                return [], None
            tracks_raw = playlist.get("tracks", [])
            if not tracks_raw:
                print(f"⚠️ playlist_detail 无 tracks 数据")
                return [], None
            songs = [self._build_song_item(s) for s in tracks_raw]
            print(f"✅ 排行榜获取成功：{len(songs)} 首歌曲")
            return songs, None
        except Exception as e:
            return [], f"解析排行榜详情失败：{e}"

    # ---------- 相似歌曲推荐 ----------
    def get_similar_songs(self, song_id, limit=5):
        """获取相似歌曲（基于网易云相似音乐接口）"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/v1/discovery/simiSong",
                                         {"songid": song_id, "limit": 50, "offset": 0})
                if raw and raw.get("code") == 200:
                    songs_raw = raw.get("songs", []) or []
                    return [self._build_song_item(s) for s in songs_raw[:limit]]
            except Exception as e:
                print(f"⚠️ Python eapi 获取相似歌曲失败，回退：{e}")
        result = self._api.request("/simi/song", {"id": song_id})
        try:
            code = result.get("code", 0) if isinstance(result, dict) else 0
            if code != 200:
                return []
            inner = result.get("data", {})
            songs_raw = inner.get("songs", []) if isinstance(inner, dict) else []
            return [self._build_song_item(s) for s in songs_raw[:limit]]
        except Exception as e:
            print(f"获取相似歌曲失败 (song_id={song_id}): {e}")
            return []

    # ---------- 歌曲详情 ----------
    def get_songs_by_ids(self, song_ids):
        """通过 ID 列表获取歌曲信息"""
        ids_list = song_ids if isinstance(song_ids, list) else [song_ids]
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/v3/song/detail", {
                    "c": "[" + ",".join('{"id":%s}' % i for i in ids_list) + "]",
                    "ids": "[" + ",".join(str(i) for i in ids_list) + "]",
                })
                if raw and raw.get("code") == 200:
                    songs_raw = raw.get("songs", []) or []
                    return [self._build_song_item(s) for s in songs_raw]
            except Exception as e:
                print(f"⚠️ Python eapi 获取歌曲详情失败，回退：{e}")
        # 回退 JS 库（weapi 接口；ids 必须是逗号分隔字符串）
        ids_str = ",".join(str(i) for i in ids_list)
        result = self._api.request("/song/detail", {"ids": ids_str})
        try:
            data = result.get("body", result) if isinstance(result, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                return []
            songs_raw = data.get("songs", [])
            return [self._build_song_item(s) for s in songs_raw]
        except Exception as e:
            print(f"获取歌曲详情失败：{e}")
            return []

    # ---------- 歌词 ----------
    def get_lyric(self, song_id):
        """获取歌词原文，返回 LRC 文本"""
        if HAS_CRYPTO:
            try:
                raw = self._eapi_request("/song/lyric",
                                         {"id": song_id, "tv": -1, "lv": -1,
                                          "rv": -1, "kv": -1})
                if raw and raw.get("code") == 200:
                    lrc = raw.get("lrc", {}) or {}
                    return lrc.get("lyric", "") or ""
            except Exception as e:
                print(f"⚠️ Python eapi 获取歌词失败，回退：{e}")
        result = self._api.request("/lyric", {"id": song_id})
        try:
            data = result.get("body", result) if isinstance(result, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                return ""
            inner = data.get("data", {}) or {}
            lrc = inner.get("lrc", {}) or {}
            return lrc.get("lyric", "") or ""
        except:
            return ""

    # ---------- 播放地址 ----------
    def get_song_url(self, song_id, level="standard"):
        """获取歌曲播放地址，返回 (url, size, freeTrialInfo, error)"""
        result = self._api.request("/song/url/v1", {"id": song_id, "level": level})
        try:
            data = result.get("body", result) if isinstance(result, dict) else {}
            code = data.get("code", 0)
            if code != 200:
                return None, 0, None, f"获取播放地址失败 (code={code})"
            inner = data.get("data", {})
            url_list = inner.get("data", [])
            if url_list:
                item = url_list[0]
                if item.get("url") and item.get("code") == 200:
                    trial = item.get("freeTrialInfo")
                    if trial is not None:
                        print(f"⚠️ 歌曲 {song_id} 为试听片段")
                    return item["url"], item.get("size", 0), trial, None
            return None, 0, None, "歌曲无版权或暂无播放地址"
        except Exception as e:
            return None, 0, None, f"解析播放地址失败：{e}"

    # ---------- 下载到缓存 ----------
    def download_to_cache(self, song_id, url, expected_size=0):
        """下载在线音频到本地缓存并校验大小，返回本地文件路径"""
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        local_path = os.path.join(self.cache_dir, f"{song_id}.mp3")
        if os.path.exists(local_path):
            actual = os.path.getsize(local_path)
            if expected_size > 0 and actual < expected_size * 0.8:
                print(f"⚠️ 缓存文件 {song_id}.mp3 不完整 ({actual}/{expected_size})，重新下载")
                os.remove(local_path)
            else:
                return local_path  # 已有有效缓存
        try:
            import requests
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            content = r.content
            if expected_size > 0 and len(content) < expected_size * 0.5:
                print(f"⚠️ 下载文件过小 ({len(content)}/{expected_size})，可能为试听片段")
            with open(local_path, "wb") as f:
                f.write(content)
            return local_path
        except Exception as e:
            print(f"下载歌曲 {song_id} 失败：{e}")
            return None

    # ---------- 封面下载 ----------
    def download_cover(self, cover_url, song_id):
        """下载封面到本地缓存，返回本地路径"""
        if not self.cache_dir or not cover_url:
            return None
        cover_dir = os.path.join(self.cache_dir, "covers")
        os.makedirs(cover_dir, exist_ok=True)
        local_path = os.path.join(cover_dir, f"{song_id}.jpg")
        if os.path.exists(local_path):
            return local_path
        try:
            import requests
            r = requests.get(cover_url, timeout=15)
            r.raise_for_status()
            content = self._strip_png_iccp(r.content)
            with open(local_path, "wb") as f:
                f.write(content)
            return local_path
        except Exception as e:
            print(f"下载封面 {song_id} 失败：{e}")
            return None

    @staticmethod
    def _strip_png_iccp(content):
        """剥离 PNG 内嵌的 iCCP 块（零依赖）。

        部分网易云封面实为 PNG 且带错误的 sRGB ICC profile，
        Qt/libpng 加载时会输出 "libpng warning: iCCP: known incorrect sRGB profile"。
        通过移除 iCCP chunk 消除该警告。
        """
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return content  # 非 PNG，原样返回
        out = bytearray(content[:8])
        pos = 8
        n = len(content)
        while pos + 12 <= n:
            length = int.from_bytes(content[pos:pos + 4], "big")
            total = 12 + length
            if pos + total > n:
                break
            if content[pos + 4:pos + 8] != b"iCCP":
                out += content[pos:pos + total]
            pos += total
        return bytes(out)

    # ---------- 缓存清理（LRU：保留最近 N 首，总量不超过上限，每次播放即时清理） ----------
    _MAX_CACHE_SONGS = 20        # 最多保留的歌曲缓存数量
    _MAX_CACHE_MB = 300          # 缓存目录总大小上限（含封面）

    def record_play(self, song_id):
        """记录播放；每次播放后按 LRU 即时清理超限缓存，不再等阈值"""
        if not self.cache_dir:
            return
        history = self._load_history()
        if song_id in history:
            history.remove(song_id)
        history.append(song_id)
        self._cleanup_cache(history)
        self._save_history(history)

    def _load_history(self):
        """读取播放历史（最近播放的排后面）"""
        history = []
        p = os.path.join(self.cache_dir, "history.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    history = [str(x) for x in data]
            except Exception:
                history = []
        return history

    def _save_history(self, history):
        p = os.path.join(self.cache_dir, "history.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(history, f)
        except Exception:
            pass

    def _cleanup_cache(self, history):
        """超数量/超总量时，按最旧优先删除歌曲缓存（含封面）"""
        # 1. 超过歌曲数量上限
        while len(history) > self._MAX_CACHE_SONGS:
            self._remove_cache(history.pop(0))
        # 2. 超过总大小上限（最旧优先，递减估算避免重复遍历）
        total = self._cache_size_mb()
        if total <= self._MAX_CACHE_MB:
            return
        for old in list(history):
            if total <= self._MAX_CACHE_MB:
                break
            mp3 = os.path.join(self.cache_dir, f"{old}.mp3")
            if os.path.exists(mp3):
                try:
                    total -= os.path.getsize(mp3) / (1024.0 * 1024.0)
                except OSError:
                    pass
            history.remove(old)
            self._remove_cache(old)

    def _cache_size_mb(self):
        """当前缓存目录总大小（MB）"""
        total = 0
        if self.cache_dir and os.path.isdir(self.cache_dir):
            for dirpath, _, files in os.walk(self.cache_dir):
                for fn in files:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, fn))
                    except OSError:
                        pass
        return total / (1024.0 * 1024.0)

    def cleanup_orphans(self):
        """启动时清理历史记录之外的遗留缓存与孤立封面
        （旧机制遗留、下载未播完、无主封面等，避免缓存无限膨胀）"""
        if not self.cache_dir or not os.path.isdir(self.cache_dir):
            return
        history = set(self._load_history())
        # 1. 不在历史里的 mp3
        try:
            for fn in os.listdir(self.cache_dir):
                if fn.endswith(".mp3") and fn[:-4] not in history:
                    try:
                        os.remove(os.path.join(self.cache_dir, fn))
                        print(f"🧹 清理孤立缓存：{fn}")
                    except OSError:
                        pass
        except OSError:
            pass
        # 2. 无对应 mp3 的封面
        cover_dir = os.path.join(self.cache_dir, "covers")
        if os.path.isdir(cover_dir):
            try:
                for fn in os.listdir(cover_dir):
                    if fn.endswith(".jpg") and not os.path.exists(
                            os.path.join(self.cache_dir, fn[:-4] + ".mp3")):
                        try:
                            os.remove(os.path.join(cover_dir, fn))
                            print(f"🧹 清理孤立封面：{fn}")
                        except OSError:
                            pass
            except OSError:
                pass

    def _remove_cache(self, song_id):
        """删除某首歌的缓存文件和封面"""
        for name in [f"{song_id}.mp3", f"covers/{song_id}.jpg"]:
            p = os.path.join(self.cache_dir, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    print(f"🧹 清理缓存：{name}")
                except:
                    pass

    # ---------- 工具 ----------
    @staticmethod
    def _fmt_duration(sec):
        if sec is None or sec <= 0:
            return "--:--"
        m = sec // 60
        s = sec % 60
        return f"{m:02d}:{s:02d}"
