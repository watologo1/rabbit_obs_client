#
# spec file for package rabbit_obs_client
#
# Copyright (c) 2019 SUSE LINUX GmbH, Nuernberg, Germany.
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via https://bugs.opensuse.org/
#


Name:           rabbit_obs_client
Version:        1.0+git20200326.f66752b
Release:        0
Summary:        Install and test packages on OpenSUSE Build Service build events
License:        GPL-3.0
Group:          System/Monitoring
Url:            https://github.com/rabbit_obs_client/rabbit_obs_client
Source:         %{name}-%{version}.tar.xz
BuildRequires:  pkgconfig(systemd)
Requires:       python3-pika
BuildArch:      noarch
%{?systemd_requires}

%description
Use this package for automated tests, which are triggered once the OBS server
has performed a successful build.
OpenSUSE Build Service rabbit client is a systemd service written in python.
It listens to rabbitmq build_success events from an OBS server.
If an event is captured that matches a package in the config file, the newly
built package rpm is retrieved from the OBS server, the local system is updated
with the package and a specified command which may start tests is executed and logged.

%prep
%autosetup

%build


%install
install -D -m 0755 rabbit_obs_client.py  %{buildroot}/usr/share/%{name}/rabbit_obs_client
install -D -m 0644 rabbit_obs_client.service %{buildroot}%{_unitdir}/rabbit_obs_client.service
install -D -m 0644 rabbit_obs.conf %{buildroot}/%{_sysconfdir}/rabbit_obs.conf
mkdir -p %{buildroot}%{_sbindir}
ln -sf service %{buildroot}%{_sbindir}/rcrabbit_obs_client


%pre
%service_add_pre %{name}.service

%post
%service_add_post %{name}.service

%preun
%service_del_preun %{name}.service

%postun
%service_del_postun %{name}.service

%files
%dir /usr/share/rabbit_obs_client
/usr/share/%{name}/rabbit_obs_client
%config %{_sysconfdir}/rabbit_obs.conf
%{_unitdir}/rabbit_obs_client.service
%{_sbindir}/rcrabbit_obs_client
%ghost /run/rabbit_obs_client
%{_localstatedir}/log/rabbit_obs

%changelog
