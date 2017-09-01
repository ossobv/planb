BYTE_UNITS = (
    ('{:.1f} KB', 1 << 10),
    ('{:.1f} MB', 1 << 20),
    ('{:.1f} GB', 1 << 30),
    ('{:.1f} TB', 1 << 40),
    ('{:.1f} PB', 1 << 50),
)


def bytes(bytes_):
    prevfmt, prevsize = '{:.0f} B', 1
    for unitfmt, unitsize in BYTE_UNITS:
        if bytes_ < unitsize:
            return prevfmt.format(bytes_ / prevsize)  # float-div in py3
        prevfmt, prevsize = unitfmt, unitsize
    return prevfmt.format(bytes_ / prevsize)


def seconds(seconds_):
    if seconds_ < 60:
        return '{}s'.format(seconds_)
    return '{:d}h {:02.0f}m'.format(seconds_ // 3600, round((seconds_ % 3600) / 60))
