"""Application entry point. Run with --help for available commands."""

import argparse
from getpass import getpass
from pathlib import Path
import sys

from retail_dw.settings import RuntimeSettings


def positive_integer(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Enter a positive integer.")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description="Retail warehouse application")
    parser.add_argument("command", choices=["check-db", "clean"], help="Check database setup or clean the source workbook")
    project_dir = Path(__file__).resolve().parent.parent
    parser.add_argument("--source-file", type=Path, default=project_dir / "Data" / "Online Retail.xlsx", help="Original UCI workbook for the clean command")
    parser.add_argument("--output-dir", type=Path, default=project_dir / "Data", help="Directory for cleaned_sales.csv")
    parser.add_argument("--host", help="Database host (otherwise prompted)")
    parser.add_argument("--port", type=positive_integer, help="Database port (otherwise prompted)")
    parser.add_argument("--database", help="Database name (otherwise prompted)")
    parser.add_argument("--user", help="Database username (otherwise prompted)")
    parser.add_argument("--online-file", type=Path, help="Optional online CSV path to validate")
    parser.add_argument("--peak-rate", type=positive_integer, default=80)
    parser.add_argument("--non-peak-rate", type=positive_integer, default=10)
    parser.add_argument("--seconds-per-hour", type=positive_integer, default=60)
    parser.add_argument("--arrival-mode", choices=["peak", "non-peak"], default="non-peak")
    return parser


def prompt_value(label, default=None):
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    raise ValueError(f"{label} is required.")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "clean":
            from retail_dw.prepare_data import clean_transactions

            print(f"Reading source workbook: {args.source_file}", flush=True)
            counts, output_file = clean_transactions(args.source_file, args.output_dir)
            counts.print_summary()
            print(f"Cleaned transactions: {output_file}")
            return 0
        if args.port is not None and args.port > 65535:
            raise ValueError("Database port must be between 1 and 65535.")
        if args.online_file is not None and not args.online_file.is_file():
            raise ValueError(f"Online file does not exist: {args.online_file}")
        from retail_dw.database import DatabaseConfig, check_database
        from mysql.connector import Error as MySQLError
    except ImportError:
        print("Install application dependencies with: python -m pip install -r Mesh-Join/requirements.txt", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCleaning cancelled.", file=sys.stderr)
        return 130

    try:
        settings = RuntimeSettings(
            peak_rate=args.peak_rate,
            non_peak_rate=args.non_peak_rate,
            seconds_per_hour=args.seconds_per_hour,
            arrival_mode=args.arrival_mode,
        )
        config = DatabaseConfig(
            host=args.host if args.host is not None else prompt_value("Host", "127.0.0.1"),
            port=args.port if args.port is not None else int(prompt_value("Port", "3306")),
            database=args.database if args.database is not None else prompt_value("Database", "retail_data_warehouse"),
            user=args.user if args.user is not None else prompt_value("Username"),
            password=getpass("Password: "),
        )
        settings.print_summary()
        version, database = check_database(config)
        print(f"Connected to MySQL {version}; database {database}.")
        print("All 10 required project tables are present.")
        if args.online_file is not None:
            print(f"Online file: {args.online_file.resolve()}")
        return 0
    except MySQLError as error:
        print(f"Database connection/check failed (MySQL {error.errno}). Check server, credentials, database, and permissions.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nStartup cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
