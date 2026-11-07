"""ビート解析キャッシュ（BeatCache）の検証。

- cache_key が音声指纹 + プロバイダ（id / 版数 / params）で変わる
- save / load の JSON 往復と空ビートの不採用
- analyze の再利用・force 再解析・未知 id フォールバック
- audio_info_from_result の後方互換（省略可能 kwargs）
"""
from __future__ import annotations

import json

from rhythmslide.core.beatcache import (
    BeatCache,
    audio_info_from_result,
    cache_key,
)
from rhythmslide.core.analyzer import AnalysisResult
from rhythmslide.core.beatproviders import DEFAULT_PROVIDER_ID
from rhythmslide.core.models import AudioInfo


# --- cache_key ------------------------------------------------------------
def test_cache_key_stable_and_file_bound(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x" * 100)
    k1 = cache_key(f)
    k2 = cache_key(str(f))
    assert k1 == k2
    f.write_bytes(b"y" * 100)
    assert cache_key(f) != k1


def test_cache_key_varies_by_provider_and_params(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x" * 100)
    base = cache_key(f, "acf", "1", {})
    assert cache_key(f, "funscript", "1", {}) != base
    assert cache_key(f, "acf", "2", {}) != base
    assert cache_key(f, "acf", "1", {"mode": "peak"}) != base
    # キー順が違っても params は正規化して同一キー
    assert (
        cache_key(f, "acf", "1", {"a": 1, "b": 2})
        == cache_key(f, "acf", "1", {"b": 2, "a": 1})
    )
    # 辞書でない params も落ちない
    assert cache_key(f, "acf", "1", "bad")


def test_cache_key_missing_file_is_stable(tmp_path):
    missing = tmp_path / "gone.mp3"
    assert cache_key(missing) == cache_key(missing)


# --- save / load ----------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    cache = BeatCache(tmp_path / "cache")
    info = AudioInfo(
        path="/a.mp3", duration=6.0, bpm=120.0, confidence=0.8,
        beats=[0.0, 0.5, 1.0, 1.5], method="beat", cache_key="k1",
        provider="acf", provider_params={},
    )
    cache.save(info)
    got = cache.load("k1")
    assert got is not None
    assert got.beats == [0.0, 0.5, 1.0, 1.5]
    assert got.provider == "acf"
    assert cache.load("nokey") is None


def test_save_skips_empty_key_and_load_rejects_empty_beats(tmp_path):
    cache = BeatCache(tmp_path / "cache")
    cache.save(AudioInfo(path="/a.mp3", beats=[0.0, 1.0], cache_key=""))
    cache_dir = tmp_path / "cache"
    assert not cache_dir.exists() or list(cache_dir.glob("*.json")) == []
    empty = tmp_path / "cache" / "k2.json"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text(json.dumps({"path": "/a.mp3", "beats": []}), encoding="utf-8")
    assert cache.load("k2") is None
    broken = tmp_path / "cache" / "k3.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert cache.load("k3") is None


# --- analyze --------------------------------------------------------------
def test_analyze_reuses_cache(click_wav, tmp_path):
    path, bpm, _ = click_wav
    cache = BeatCache(tmp_path / "cache")
    first = cache.analyze(path, "acf", {})
    assert first.cache_key
    assert abs(first.bpm - bpm) <= 3.0
    assert first.provider == "acf"
    second = cache.analyze(path, "acf", {})
    assert second.cache_key == first.cache_key
    # JSON 保存時に beats は小数 4 桁へ丸められる（往復許容）
    assert len(second.beats) == len(first.beats)
    for a, b in zip(second.beats, first.beats):
        assert abs(a - b) <= 1e-4
    # プロバイダを替えれば別キーで再解析される（FunScript は隣接ファイルを使用）
    (tmp_path / "click.funscript").write_text(json.dumps({
        "actions": [
            {"at": 0, "pos": 0}, {"at": 500, "pos": 100}, {"at": 1000, "pos": 0},
            {"at": 1500, "pos": 100}, {"at": 2000, "pos": 0}, {"at": 2500, "pos": 100},
        ],
        "version": "1.0",
    }), encoding="utf-8")
    fs = cache.analyze(path, "funscript", {})
    assert fs.cache_key != first.cache_key
    assert fs.beats == [0.5, 1.5, 2.5]
    assert fs.provider == "funscript"
    # 保存済み JSON にも provider / provider_params（sanitize 済みの正規形）が入る
    saved = json.loads((tmp_path / "cache" / f"{fs.cache_key}.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "funscript"
    assert saved["provider_params"]["mode"] == "up"
    assert saved["provider_params"]["path"] == ""


def test_analyze_force_reanalyzes(click_wav, tmp_path):
    path, _, _ = click_wav
    cache = BeatCache(tmp_path / "cache")
    first = cache.analyze(path, "acf", {})
    # force=True はキャッシュを使わず再解析する（結果のキーは同じ）
    second = cache.analyze(path, "acf", {}, force=True)
    assert second.cache_key == first.cache_key
    assert second.beats == first.beats


def test_analyze_unknown_provider_falls_back(click_wav, tmp_path):
    path, bpm, _ = click_wav
    cache = BeatCache(tmp_path / "cache")
    info = cache.analyze(path, "no_such", {"bpm": 999})
    assert info.provider == DEFAULT_PROVIDER_ID
    assert abs(info.bpm - bpm) <= 3.0


def test_analyze_funscript_no_audio_needed(tmp_path):
    # 音声は中身だけ違うダミー（デコードされないが指紋は別になる）
    (tmp_path / "a.mp3").write_bytes(b"x" * 16)
    (tmp_path / "b.mp3").write_bytes(b"y" * 16)
    fs = tmp_path / "a.funscript"
    fs.write_text(json.dumps({
        "actions": [
            {"at": 0, "pos": 0}, {"at": 500, "pos": 100}, {"at": 1000, "pos": 0},
            {"at": 1500, "pos": 100}, {"at": 2000, "pos": 0}, {"at": 2500, "pos": 100},
        ],
        "version": "1.0",
    }), encoding="utf-8")
    cache = BeatCache(tmp_path / "cache")
    info = cache.analyze(tmp_path / "a.mp3", "funscript", {})
    assert info.provider == "funscript"
    assert info.beats == [0.5, 1.5, 2.5]
    assert info.method == "beat"
    # 空ビート（読み込み失敗）はキャッシュに保存しない
    miss = cache.analyze(tmp_path / "b.mp3", "funscript", {})
    assert miss.beats == []
    assert miss.cache_key
    assert cache.load(miss.cache_key) is None


def test_analyze_funscript_missing_falls_back_and_not_cached(click_wav, tmp_path):
    """FunScript 未検出なら ACF にフォールバックし、キャッシュは作らない。

    同じキーでキャッシュすると、後で FunScript ファイルを置いても古い
    ACF 結果がヒットしてしまうため。
    """
    path, bpm, _ = click_wav
    cache_dir = tmp_path / "cache"
    cache = BeatCache(cache_dir)
    info = cache.analyze(path, "funscript", {})
    assert info.provider == "acf"
    assert info.beats
    assert abs(info.bpm - bpm) <= 3.0
    assert not (cache_dir / f"{info.cache_key}.json").exists()

    # FunScript ファイルを置けば（force なしでも）FunScript として読める
    (tmp_path / "click.funscript").write_text(json.dumps({
        "actions": [
            {"at": 0, "pos": 0}, {"at": 500, "pos": 100}, {"at": 1000, "pos": 0},
            {"at": 1500, "pos": 100}, {"at": 2000, "pos": 0}, {"at": 2500, "pos": 100},
        ],
        "version": "1.0",
    }), encoding="utf-8")
    info2 = cache.analyze(path, "funscript", {})
    assert info2.provider == "funscript"
    assert info2.beats == [0.5, 1.5, 2.5]


# --- audio_info_from_result -----------------------------------------------
def test_audio_info_from_result_optional_kwargs():
    res = AnalysisResult(duration=6.0, bpm=120.0, beats=[0.0, 0.5],
                         confidence=0.7, method="beat")
    # 旧呼び出し（位置引数 2 つ）でも壊れない
    info = audio_info_from_result("/a.mp3", res)
    assert info.provider == DEFAULT_PROVIDER_ID
    assert info.provider_params == {}
    assert info.cache_key == ""
    info2 = audio_info_from_result("/a.mp3", res, "k", provider_id="funscript",
                                   provider_params={"mode": "peak"})
    assert info2.cache_key == "k"
    assert info2.provider == "funscript"
    assert info2.provider_params == {"mode": "peak"}
