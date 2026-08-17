import argparse
import logging

from taiga.attach_tickets import attach_tickets
from taiga.auto_move_test import auto_move_test
from taiga.sorter import sort

function_mapping = {
    "sort": sort,
    "attach_tickets": attach_tickets,
    "auto_move_tested": auto_move_test,
}


def main():
    parser = argparse.ArgumentParser(description="Edain Taiga board maintenance tasks.")
    parser.add_argument("command", choices=sorted(function_mapping))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    function_mapping[args.command]()


if __name__ == "__main__":
    main()
