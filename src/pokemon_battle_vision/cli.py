"""Checkpoint 1A、ROI approval 與 Checkpoint 1B 本地端 commands。"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .errors import CheckpointError
from .pipeline import run_checkpoint_1a
from .roi import create_roi_approval
from .scanner import run_checkpoint_1b


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pokemon-battle-vision",
        description="Pokémon Battle Vision Milestone 1 — Checkpoint 1A/1B",
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
            )
            print("Checkpoint 1B 已完成：{}".format(args.output / "events.json"))
            print("10 Hz sampled frames：{}".format(report["counts"]["sampled_frames"]))
            print("event candidates：{}".format(report["counts"]["event_candidates"]))
            return 0
        parser.error("未知 command")
    except CheckpointError as exc:
        print("錯誤：{}".format(exc), file=sys.stderr)
        return exc.exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
