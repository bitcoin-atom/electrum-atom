#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2011 thomasv@gitorious
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


import random, socket, ast, re
import threading, traceback, sys, time, json, Queue

from version import ELECTRUM_VERSION
from util import print_error


DEFAULT_TIMEOUT = 5
DEFAULT_SERVERS = [ 
    'electrum.novit.ro:50001:t', 
    'electrum.pdmc.net:50001:t',
    'ecdsa.org:50001:t',
    'electrum.bitcoins.sk:50001:t',
    'uncle-enzo.info:50001:t',
    'electrum.bytesized-hosting.com:50001:t',
    'electrum.bitcoin.cz:50001:t',
    'electrum.bitfoo.org:50001:t'
    ]

proxy_modes = ['socks4', 'socks5', 'http']


def pick_random_server():
    return random.choice( DEFAULT_SERVERS )




class Interface(threading.Thread):

    def register_callback(self, update_callback):
        with self.lock:
            self.update_callbacks.append(update_callback)

    def trigger_callbacks(self):
        with self.lock:
            callbacks = self.update_callbacks[:]
        [update() for update in callbacks]


    def init_server(self, host, port, proxy=None, use_ssl=True):
        self.host = host
        self.port = port
        self.proxy = proxy
        self.use_ssl = use_ssl

        self.servers = [] # actual list from IRC
        self.rtime = 0
        self.bytes_received = 0

        self.is_connected = True
        self.poll_interval = 1

        #json
        self.message_id = 0
        self.unanswered_requests = {}



    def queue_json_response(self, c):

        # uncomment to debug
        # print "<--",c

        msg_id = c.get('id')
        error = c.get('error')
        
        if error:
            print "received error:", c
            return

        if msg_id is not None:
            with self.lock: 
                method, params, channel = self.unanswered_requests.pop(msg_id)
            result = c.get('result')
        else:
            # notification. we should find the channel(s)..
            method = c.get('method')
            params = c.get('params')
            with self.lock:
                for k,v in self.subscriptions.items():
                    if (method, params) in v:
                        channel = k
                else:
                    raise

            if method == 'blockchain.numblocks.subscribe':
                result = params[0]
                params = []

            elif method == 'blockchain.address.subscribe':
                addr = params[0]
                result = params[1]
                params = [addr]

        response_queue = self.responses[channel]
        response_queue.put({'method':method, 'params':params, 'result':result, 'id':msg_id})



    def get_response(self, channel='default', block=True, timeout=10000000000):
        return self.responses[channel].get(block, timeout)

    def register_channel(self, channel):
        with self.lock:
            self.responses[channel] = Queue.Queue()

    def poke(self, channel):
        self.responses[channel].put(None)


    def init_http(self, host, port, proxy=None, use_ssl=True):
        self.init_server(host, port, proxy, use_ssl)
        self.session_id = None
        self.connection_msg = ('https' if self.use_ssl else 'http') + '://%s:%d'%( self.host, self.port )


    def run_http(self):
        self.is_connected = True
        while self.is_connected:
            try:
                if self.session_id:
                    self.poll()
                time.sleep(self.poll_interval)
            except socket.gaierror:
                break
            except socket.error:
                break
            except:
                traceback.print_exc(file=sys.stdout)
                break
            
        self.is_connected = False

                
    def poll(self):
        self.send([])


    def send_http(self, messages, channel='default'):
        import urllib2, json, time, cookielib
        
        if self.proxy:
            import socks
            socks.setdefaultproxy(proxy_modes.index(self.proxy["mode"]), self.proxy["host"], int(self.proxy["port"]) )
            socks.wrapmodule(urllib2)

        cj = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
        urllib2.install_opener(opener)

        t1 = time.time()

        data = []
        for m in messages:
            method, params = m
            if type(params) != type([]): params = [params]
            data.append( { 'method':method, 'id':self.message_id, 'params':params } )
            self.unanswered_requests[self.message_id] = method, params, channel
            self.message_id += 1

        if data:
            data_json = json.dumps(data)
        else:
            # poll with GET
            data_json = None 

            
        headers = {'content-type': 'application/json'}
        if self.session_id:
            headers['cookie'] = 'SESSION=%s'%self.session_id

        req = urllib2.Request(self.connection_msg, data_json, headers)
        response_stream = urllib2.urlopen(req)

        for index, cookie in enumerate(cj):
            if cookie.name=='SESSION':
                self.session_id = cookie.value

        response = response_stream.read()
        self.bytes_received += len(response)
        if response: 
            response = json.loads( response )
            if type(response) is not type([]):
                self.queue_json_response(response)
            else:
                for item in response:
                    self.queue_json_response(item)

        if response: 
            self.poll_interval = 1
        else:
            if self.poll_interval < 15: 
                self.poll_interval += 1
        #print self.poll_interval, response

        self.rtime = time.time() - t1
        self.is_connected = True




    def init_tcp(self, host, port, proxy=None, use_ssl=True):
        self.init_server(host, port, proxy, use_ssl)

        import ssl
        global proxy_modes
        self.connection_msg = "%s:%d"%(self.host,self.port)
        if self.proxy is None:
            s = socket.socket( socket.AF_INET, socket.SOCK_STREAM )
        else:
            self.connection_msg += " using proxy %s:%s:%s"%(self.proxy.get('mode'), self.proxy.get('host'), self.proxy.get('port'))
            import socks
            s = socks.socksocket()
            s.setproxy(proxy_modes.index(self.proxy["mode"]), self.proxy["host"], int(self.proxy["port"]) )

        if self.use_ssl:
            s = ssl.wrap_socket(s, ssl_version=ssl.PROTOCOL_SSLv23)
            
        s.settimeout(2)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        try:
            s.connect(( self.host.encode('ascii'), int(self.port)))
            s.settimeout(60)
            self.s = s
            self.is_connected = True
        except:
            self.is_connected = False
            self.s = None


    def run_tcp(self):
        try:
            out = ''
            while self.is_connected:
                try: msg = self.s.recv(1024)
                except socket.timeout:
                    print "timeout"
                    # ping the server with server.version, as a real ping does not exist yet
                    self.send([('server.version', [ELECTRUM_VERSION])])
                    continue
                out += msg
                self.bytes_received += len(msg)
                if msg == '': 
                    self.is_connected = False
                    print "Disconnected."

                while True:
                    s = out.find('\n')
                    if s==-1: break
                    c = out[0:s]
                    out = out[s+1:]
                    c = json.loads(c)
                    self.queue_json_response(c)

        except:
            traceback.print_exc(file=sys.stdout)

        self.is_connected = False


    def send_tcp(self, messages, channel='default'):
        """return the ids of the requests that we sent"""
        out = ''
        ids = []
        for m in messages:
            method, params = m 
            request = json.dumps( { 'id':self.message_id, 'method':method, 'params':params } )
            self.unanswered_requests[self.message_id] = method, params, channel
            ids.append(self.message_id)
            # uncomment to debug
            # print "-->",request
            self.message_id += 1
            out += request + '\n'
        while out:
            sent = self.s.send( out )
            out = out[sent:]
        return ids



    def __init__(self, config=None, loop=False, servers_loaded_callback=None):

        if config is None:
            from simple_config import SimpleConfig
            config = SimpleConfig()

        threading.Thread.__init__(self)
        self.daemon = True
        self.loop = loop
        self.config = config
        self.servers_loaded_callback = servers_loaded_callback

        self.subscriptions = {}
        self.responses = {}
        self.responses['default'] = Queue.Queue()

        self.update_callbacks = []
        self.lock = threading.Lock()
        self.init_interface()



    def init_interface(self):
        if self.config.get('server'):
            self.init_with_server(self.config)
        else:
            print "Using random server..."
            servers = DEFAULT_SERVERS
            while servers:
                server = random.choice( servers )
                servers.remove(server)
                self.config.set_key('server', server, False)
                self.init_with_server(self.config)
                if self.is_connected: break

            if not servers:
                raise BaseException('no server available')

        if self.is_connected:
            print "Connected to " + self.connection_msg
            self.send([('server.version', [ELECTRUM_VERSION])])
            #self.send([('server.banner',[])], 'synchronizer')
        else:
            print_error("Failed to connect " + self.connection_msg)


    def init_with_server(self, config):
            
        s = config.get('server')
        host, port, protocol = s.split(':')
        port = int(port)

        self.protocol = protocol
        proxy = self.parse_proxy_options(config.get('proxy'))
        self.server = host + ':%d:%s'%(port, protocol)

        #print protocol, host, port
        if protocol in 'st':
            self.init_tcp(host, port, proxy, use_ssl=(protocol=='s'))
        elif protocol in 'gh':
            self.init_http(host, port, proxy, use_ssl=(protocol=='g'))
        else:
            raise BaseException('Unknown protocol: %s'%protocol)


    def send(self, messages, channel='default'):

        sub = []
        for message in messages:
            m, v = message
            if m[-10:] == '.subscribe':
                sub.append(message)

        if sub:
            with self.lock:
                if self.subscriptions.get(channel) is None: 
                    self.subscriptions[channel] = []
                self.subscriptions[channel] += sub

        if self.protocol in 'st':
            with self.lock:
                out = self.send_tcp(messages, channel)
        else:
            # do not use lock, http is synchronous
            out = self.send_http(messages, channel)

        return out

    def resend_subscriptions(self):
        for channel, messages in self.subscriptions.items():
            if messages:
                self.send(messages, channel)



    def parse_proxy_options(self, s):
        if type(s) == type({}): return s  # fixme: type should be fixed
        if type(s) != type(""): return None  
        if s.lower() == 'none': return None
        proxy = { "mode":"socks5", "host":"localhost" }
        args = s.split(':')
        n = 0
        if proxy_modes.count(args[n]) == 1:
            proxy["mode"] = args[n]
            n += 1
        if len(args) > n:
            proxy["host"] = args[n]
            n += 1
        if len(args) > n:
            proxy["port"] = args[n]
        else:
            proxy["port"] = "8080" if proxy["mode"] == "http" else "1080"
        return proxy


    def set_server(self, server, proxy=None):
        # raise an error if the format isnt correct
        a,b,c = server.split(':')
        b = int(b)
        assert c in 'stgh'
        # set the server
        if server != self.server or proxy != self.proxy:
            print "changing server:", server, proxy
            self.server = server
            self.proxy = proxy
            if self.protocol in 'st':
                self.s.shutdown(socket.SHUT_RDWR)
                self.s.close()
            self.is_connected = False  # this exits the polling loop


    def is_empty(self, channel):
        q = self.responses.get(channel)
        if q: 
            return q.empty()
        else:
            return True


    def get_pending_requests(self, channel):
        result = []
        with self.lock:
            for k, v in self.unanswered_requests.items():
                a, b, c = v
                if c == channel: result.append(k)
        return result

    def is_up_to_date(self, channel):
        return self.is_empty(channel) and not self.get_pending_requests(channel)


    def synchronous_get(self, requests, timeout=100000000):
        # todo: use generators, unanswered_requests should be a list of arrays...
        ids = self.send(requests)
        id2 = ids[:]
        res = {}
        while ids:
            r = self.responses['default'].get(True, timeout)
            _id = r.get('id')
            if _id in ids:
                ids.remove(_id)
                res[_id] = r.get('result')
        out = []
        for _id in id2:
            out.append(res[_id])
        return out



    def run(self):
        while True:
            self.run_tcp() if self.protocol in 'st' else self.run_http()
            self.trigger_callbacks()
            if not self.loop: break

            time.sleep(5)
            self.init_interface()
            self.resend_subscriptions()



