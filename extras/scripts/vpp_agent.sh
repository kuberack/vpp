#!/bin/bash

#get the ipaddress associated with host name
hostname=$(hostname)
node_ip=$(grep $hostname /config/node/ip | awk -F'=' '{print $2}')

#bring up the interface. Dataplane interface is named eth0 within vpp
vppctl set interface state eth0 up

#setup the ip address
vppctl set interface ip address eth0 $node_ip

#setup the gateway ip
gateway=$(grep gateway /config/node/ip | awk -F'=' '{print $2}')
vppctl ip route add 0.0.0.0/0 via $gateway

#infinite loop
while true
do
  echo "Press [CTRL+C] to stop.."
  sleep 3600
done
