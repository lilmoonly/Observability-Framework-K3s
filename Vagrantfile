nodes = [
  { :name => "k8s-ctrl",     :ip => "192.168.56.10", :mem => 2048, :cpu => 2 },
  { :name => "app-node-1",   :ip => "192.168.56.11", :mem => 768,  :cpu => 1 },
  { :name => "db-node-1",    :ip => "192.168.56.13", :mem => 768,  :cpu => 1 },
  { :name => "db-node-2",    :ip => "192.168.56.14", :mem => 768, :cpu => 1 },
  { :name => "db-node-3",    :ip => "192.168.56.18", :mem => 768, :cpu => 1 },
  { :name => "logging-node", :ip => "192.168.56.15", :mem => 2048, :cpu => 2 },
  { :name => "monitor-node", :ip => "192.168.56.16", :mem => 2048, :cpu => 2 },
  { :name => "ai-node",      :ip => "192.168.56.17", :mem => 512,  :cpu => 1 }
]

Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"

  nodes.each do |node|
    config.vm.define node[:name] do |node_config|
      node_config.vm.hostname = node[:name]
      node_config.vm.network "private_network", ip: node[:ip]

      node_config.vm.provider "virtualbox" do |vb|
        vb.memory = node[:mem]
        vb.cpus = node[:cpu]
        vb.name = node[:name]
      end

      # Assumes ssh_pub_key is defined in the surrounding Vagrant context.
      node_config.vm.provision "shell", inline: <<-SHELL
        echo "#{ssh_pub_key}" >> /home/vagrant/.ssh/authorized_keys

        chown vagrant:vagrant /home/vagrant/.ssh/authorized_keys
        chmod 600 /home/vagrant/.ssh/authorized_keys

        apt-get update -y
        apt-get install -y python3 python3-pip
      SHELL
    end
  end
end
