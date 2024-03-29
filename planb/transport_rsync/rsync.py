RSYNC_EXITCODES = {
    1: 'Syntax or usage error',
    2: 'Protocol incompatibility',
    3: 'Errors selecting input/output files, dirs',
    4: ('Requested action not supported: an attempt was made to '
        'manipulate 64-bit files on a platform that cannot support '
        'them; oran option was specified that is supported by the '
        'client and not by the server.'),
    5: 'Error starting client-server protocol',
    6: 'Daemon unable to append to log-file',
    10: 'Error in socket I/O',
    11: 'Error in file I/O',
    12: 'Error in rsync protocol data stream',
    13: 'Errors with program diagnostics',
    14: 'Error in IPC code',
    20: 'Received SIGUSR1 or SIGINT',
    21: 'Some error returned by waitpid()',
    22: 'Error allocating core memory buffers',
    23: 'Partial transfer due to error',
    24: 'Partial transfer due to vanished source files',
    25: 'The --max-delete limit stopped deletions',
    30: 'Timeout in data send/receive',
    35: 'Timeout waiting for daemon connection',
    255: 'Unspecified error',
}

RSYNC_ERR_VANISHED_SOURCE = 24  # Partial transfer due to vanished source files
