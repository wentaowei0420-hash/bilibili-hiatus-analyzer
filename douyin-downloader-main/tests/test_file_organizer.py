from pathlib import Path

from gui_modules.file_organizer import grade_from_name, organize_download_directory


def test_grade_from_name_detects_prefix():
    assert grade_from_name("B_清妍-_196758_2021-08-17.mp4") == "B"
    assert grade_from_name("A级_作者_作品.mp4") == "A"
    assert grade_from_name("普通文件.mp4") == ""


def test_organize_download_directory_moves_by_grade_and_deletes_webp(tmp_path):
    b_file = tmp_path / "B_清妍-_196758_2021-08-17_17_03_25_阅_6997322074909986089.mp4"
    b_file.write_text("video", encoding="utf-8")
    s_dir = tmp_path / "S_作者_作品"
    s_dir.mkdir()
    (s_dir / "video.mp4").write_text("video", encoding="utf-8")
    (tmp_path / "cover.webp").write_text("webp", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "preview.WEBP").write_text("webp", encoding="utf-8")
    (tmp_path / "普通文件.mp4").write_text("video", encoding="utf-8")

    result = organize_download_directory(tmp_path)

    assert result.moved_count == 2
    assert result.deleted_webp_count == 2
    assert result.skipped_count == 2
    assert (tmp_path / "B级" / b_file.name).exists()
    assert (tmp_path / "S级" / s_dir.name / "video.mp4").exists()
    assert not list(tmp_path.rglob("*.webp"))
    assert not list(tmp_path.rglob("*.WEBP"))


def test_organize_download_directory_uses_unique_destination(tmp_path):
    target_dir = tmp_path / "B级"
    target_dir.mkdir()
    (target_dir / "B_demo.mp4").write_text("old", encoding="utf-8")
    (tmp_path / "B_demo.mp4").write_text("new", encoding="utf-8")

    result = organize_download_directory(tmp_path)

    assert result.moved_count == 1
    assert (target_dir / "B_demo.mp4").read_text(encoding="utf-8") == "old"
    assert (target_dir / "B_demo_1.mp4").read_text(encoding="utf-8") == "new"
