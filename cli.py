import sys

from taiga.attach_tickets import attach_tickets
from taiga.auto_move_test import auto_move_test
from taiga.sorter import sort

function_mapping = {
    "sort": sort,
    "attach_tickets": attach_tickets,
    "auto_move_tested": auto_move_test,
}


function_mapping[sys.argv[1]]()
