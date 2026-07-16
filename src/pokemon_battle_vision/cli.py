"""Checkpoint 1A／1B 與 Checkpoint 1C 本機 OCR commands。"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .errors import CheckpointError
from .checkpoint1c import run_checkpoint_1c
from .checkpoint1d import run_checkpoint_1d
from .checkpoint1e import run_checkpoint_1e
from .pipeline import run_checkpoint_1a
from .review_pack import build_review_pack
from .roi import create_roi_approval
from .scanner import run_checkpoint_1b


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pokemon-battle-vision",
        description="Pokémon Battle Vision Milestone 1 — Checkpoint 1A 至 1E",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "checkpoint-1a", help="產生 metadata、PTS、anchors、contact sheets 與 ROI overlays"
    )
    run_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    run_parser.add_argument("--video", type=Path, required=True)
    run_parser.add_argument("--known-frames", type=Path, required=True)
    run_parser.add_argument("--match-reference", type=Path, required=True)
    run_parser.add_argument("--screenshots-dir", type=Path, required=True)
    run_parser.add_argument("--roi-config", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--interval-sec", type=float, default=30.0)
    run_parser.add_argument("--dependency-timeout-sec", type=float, default=15.0)
    run_parser.add_argument("--ffprobe-timeout-sec", type=float, default=240.0)

    approval_parser = subparsers.add_parser(
        "approve-roi", help="人工檢查完成後，以 hashes 產生 roi_approval.json"
    )
    approval_parser.add_argument("--video", type=Path, required=True)
    approval_parser.add_argument("--roi-config", type=Path, required=True)
    approval_parser.add_argument("--overlay-manifest", type=Path, required=True)
    approval_parser.add_argument("--approved-by", required=True)
    approval_parser.add_argument("--output", type=Path, required=True)

    scan_parser = subparsers.add_parser(
        "checkpoint-1b", help="使用 Frozen ROI Baseline，以固定 10 Hz 掃描全片並建立事件候選"
    )
    scan_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    scan_parser.add_argument("--video", type=Path, required=True)
    scan_parser.add_argument("--roi-config", type=Path, required=True)
    scan_parser.add_argument("--checkpoint-1a-dir", type=Path, required=True)
    scan_parser.add_argument("--roi-approval", type=Path, required=True)
    scan_parser.add_argument("--output", type=Path, required=True)
    scan_parser.add_argument(
        "--debug-output",
        type=Path,
        help="預設為 --output 同層的 checkpoint-1b-debug",
    )

    review_parser = subparsers.add_parser(
        "build-review-pack",
        help="忠實整理 Checkpoint 1B candidates，產生人工審查 images、contact sheets 與 coverage review",
    )
    review_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    review_parser.add_argument("--video", type=Path, required=True)
    review_parser.add_argument("--events", type=Path, required=True)
    review_parser.add_argument("--frames", type=Path, required=True)
    review_parser.add_argument(
        "--diagnostics",
        type=Path,
        help="預設讀取 outputs/checkpoint-1b-debug/battle_text_diagnostics.jsonl",
    )
    review_parser.add_argument("--checkpoint-1a-dir", type=Path, required=True)
    review_parser.add_argument("--roi-config", type=Path, required=True)
    review_parser.add_argument("--output", type=Path, required=True)
    review_parser.add_argument("--coverage-interval-sec", type=float, default=0.5)

    ocr_parser = subparsers.add_parser(
        "checkpoint-1c",
        help="從 frozen Checkpoint 1B candidates 執行本機多影格 OCR、文字驗證與人工審查包",
    )
    ocr_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    ocr_parser.add_argument("--video", type=Path, required=True)
    ocr_parser.add_argument("--checkpoint-1b-dir", type=Path, required=True)
    ocr_parser.add_argument("--checkpoint-1b-review-dir", type=Path, required=True)
    ocr_parser.add_argument("--output", type=Path, required=True)
    ocr_parser.add_argument("--review-output", type=Path, required=True)

    event_parser = subparsers.add_parser(
        "checkpoint-1d",
        help="將完成審查的 Checkpoint 1C 文字轉成 BattleEvent IR",
    )
    event_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    event_parser.add_argument("--review", type=Path, required=True)
    event_parser.add_argument("--output", type=Path, required=True)

    timeline_parser = subparsers.add_parser(
        "checkpoint-1e",
        help="將 frozen BattleEvent 建立為保守關聯、可人工審查的對戰時間線",
    )
    timeline_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    timeline_parser.add_argument("--events", type=Path, required=True)
    timeline_parser.add_argument("--output", type=Path, required=True)
    timeline_parser.add_argument("--review-output", type=Path, required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "checkpoint-1a":
            report = run_checkpoint_1a(
                project_root=args.project_root,
                video_path=args.video,
                known_frames_path=args.known_frames,
                match_reference_path=args.match_reference,
                screenshots_dir=args.screenshots_dir,
                roi_config_path=args.roi_config,
                output_dir=args.output,
                interval_sec=args.interval_sec,
                dependency_timeout_sec=args.dependency_timeout_sec,
                ffprobe_timeout_sec=args.ffprobe_timeout_sec,
            )
            print(
                "Checkpoint 1A 已完成；ROI 尚未核准。請檢查 {}，不得開始 Checkpoint 1B。".format(
                    args.output / "roi_overlay_manifest.json"
                )
            )
            print("狀態：{}".format(report["status"]))
            return 0
        if args.command == "approve-roi":
            create_roi_approval(
                video_path=args.video,
                roi_config_path=args.roi_config,
                overlay_manifest_path=args.overlay_manifest,
                approved_by=args.approved_by,
                output_path=args.output,
            )
            print("ROI 核准紀錄已寫入：{}".format(args.output))
            return 0
        if args.command == "checkpoint-1b":
            report = run_checkpoint_1b(
                project_root=args.project_root,
                video_path=args.video,
                roi_config_path=args.roi_config,
                checkpoint1a_dir=args.checkpoint_1a_dir,
                roi_approval_path=args.roi_approval,
                output_dir=args.output,
                debug_output_dir=args.debug_output,
            )
            print("Checkpoint 1B 已完成：{}".format(args.output / "events.json"))
            print("10 Hz sampled frames：{}".format(report["counts"]["sampled_frames"]))
            print("event candidates：{}".format(report["counts"]["event_candidates"]))
            return 0
        if args.command == "build-review-pack":
            manifest = build_review_pack(
                project_root=args.project_root,
                video_path=args.video,
                events_path=args.events,
                frames_path=args.frames,
                checkpoint1a_dir=args.checkpoint_1a_dir,
                roi_config_path=args.roi_config,
                output_dir=args.output,
                coverage_interval_sec=args.coverage_interval_sec,
                diagnostics_path=args.diagnostics,
            )
            print("Checkpoint 1B Human Review Pack 已完成：{}".format(args.output))
            print("candidate review records：{}".format(manifest["candidate_count"]))
            print(
                "coverage pages：{}".format(manifest["coverage_review"]["page_count"])
            )
            return 0
        if args.command == "checkpoint-1c":
            manifest = run_checkpoint_1c(
                project_root=args.project_root,
                video_path=args.video,
                checkpoint1b_dir=args.checkpoint_1b_dir,
                checkpoint1b_review_dir=args.checkpoint_1b_review_dir,
                output_dir=args.output,
                review_output_dir=args.review_output,
            )
            print("Checkpoint 1C 已完成：{}".format(args.output))
            print("已處理 candidates：{}".format(manifest["processed_candidate_count"]))
            print("validation counts：{}".format(manifest["validation_counts"]))
            print("workflow counts：{}".format(manifest["workflow_counts"]))
            return 0
        if args.command == "checkpoint-1d":
            manifest = run_checkpoint_1d(
                project_root=args.project_root,
                review_path=args.review,
                output_dir=args.output,
            )
            print("Checkpoint 1D 已完成：{}".format(args.output / "battle_events.json"))
            print("Battle Events：{}".format(manifest["event_count"]))
            print("Event counts：{}".format(manifest["event_counts"]))
            print("UNKNOWN_EVENT：{}".format(manifest["unknown_count"]))
            return 0
        if args.command == "checkpoint-1e":
            manifest = run_checkpoint_1e(
                project_root=args.project_root,
                events_path=args.events,
                output_dir=args.output,
                review_output_dir=args.review_output,
            )
            print("Checkpoint 1E 已完成：{}".format(args.output / "battle_timeline.json"))
            print("Action Groups：{}".format(manifest["timeline_count"]))
            print("Relation Edges：{}".format(manifest["relation_count"]))
            print("Group status：{}".format(manifest["group_status_counts"]))
            print("Unlinked events：{}".format(manifest["unlinked_event_count"]))
            return 0
        parser.error("未知 command")
    except CheckpointError as exc:
        print("錯誤：{}".format(exc), file=sys.stderr)
        return exc.exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
