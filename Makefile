.PHONY: all reinstall uninstall-y install has-virtualenv

all:
	@echo 'Unsure what to make for all' >&2
	@false

reload:
	pkill -P1 -HUP -uplanb uwsgi
	systemctl restart planb-queue.service 

reinstall: uninstall-y install

uninstall-y: has-virtualenv
	pip uninstall -y planb
	
install: has-virtualenv
	pip install .

has-virtualenv:
	@test -n "$$VIRTUAL_ENV"
