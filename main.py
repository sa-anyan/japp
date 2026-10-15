import argparse
from pathlib import Path

from automation.sync import run_sync


def main(argv=None):
	parser = argparse.ArgumentParser(description="JAPP automation tools")
	commands = parser.add_subparsers(dest="command", required=True)
	sync_parser = commands.add_parser("sync", help="Sync company research into the dashboard")
	sync_parser.add_argument("--research-dir", type=Path, default=Path("research/companies"))
	sync_parser.add_argument(
		"--workbook",
		type=Path,
		default=Path("data/reference/JAPP_Automation_Dashboard.xlsx"),
	)
	sync_parser.add_argument("--manifest", type=Path)
	sync_parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
	sync_parser.add_argument("--dry-run", action="store_true")
	args = parser.parse_args(argv)

	if args.command == "sync":
		try:
			result = run_sync(
				research_dir=args.research_dir,
				workbook_path=args.workbook,
				manifest_path=args.manifest,
				backup_dir=args.backup_dir,
				dry_run=args.dry_run,
			)
		except (OSError, ValueError, RuntimeError) as exc:
			parser.exit(2, f"Error: {exc}\n")
		for message in result["messages"]:
			print(message)
		print(
			f"{'Dry run ' if args.dry_run else ''}Summary: "
			f"{result['added']} added, {result['updated']} updated, "
			f"{result['skipped']} skipped, {result['malformed']} malformed"
		)
		if args.dry_run:
			print("Dry run complete; workbook, manifest, and backups were not written.")
		elif result["backup_path"]:
			print(f"Workbook backup: {result['backup_path']}")
		return 0

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
