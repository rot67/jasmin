from twisted.internet import reactor, defer
from twisted.web.client import getPage
from test_router import HappySMSCTestCase, id_generator
from jasmin.routing.jasminApi import *
from jasmin.routing.proxies import RouterPBProxy
from jasmin.protocols.smpp.configs import SMPPClientConfig
from jasmin.routing.Routes import DefaultRoute
from jasmin.routing.Interceptors import DefaultInterceptor
from jasmin.interceptor.interceptor import InterceptorPB
from jasmin.interceptor.configs import InterceptorPBConfig, InterceptorPBClientConfig
from jasmin.interceptor.proxies import InterceptorPBProxy
from twisted.cred import portal
from twisted.cred.checkers import AllowAnonymousAccess, InMemoryUsernamePasswordDatabaseDontUse
from jasmin.tools.cred.portal import JasminPBRealm
from jasmin.tools.spread.pb import JasminPBPortalRoot
from twisted.spread import pb

@defer.inlineCallbacks
def waitFor(seconds):
    # Wait seconds
    waitDeferred = defer.Deferred()
    reactor.callLater(seconds, waitDeferred.callback, None)
    yield waitDeferred

class ProvisionWithoutInterceptorPB:
    script = 'Default script that generates a syntax error !'

    @defer.inlineCallbacks
    def setUp(self):
        if hasattr(self, 'ipb_client'):
            yield HappySMSCTestCase.setUp(self, self.ipb_client)
        else:
            yield HappySMSCTestCase.setUp(self)

        yield self.connect('127.0.0.1', self.pbPort)

        # Provision user, group, default mt route and
        # default mt interceptor
        self.g1 = Group(1)
        yield self.group_add(self.g1)

        self.c1 = SmppClientConnector(id_generator())
        self.mt_interceptor = MTInterceptorScript(self.script)
        self.u1_password = 'password'
        self.u1 = User(1, self.g1, 'username', self.u1_password)
        self.u2_password = 'password'
        self.u2 = User(1, self.g1, 'username2', self.u2_password)
        yield self.user_add(self.u1)

        yield self.mtroute_add(DefaultRoute(self.c1), 0)
        yield self.mtinterceptor_add(DefaultInterceptor(self.mt_interceptor), 0)

        # Now we'll create the connector
        yield self.SMPPClientManagerPBProxy.connect('127.0.0.1', self.CManagerPort)
        c1Config = SMPPClientConfig(id=self.c1.cid, port = self.SMSCPort.getHost().port)
        yield self.SMPPClientManagerPBProxy.add(c1Config)

        # And start it !
        yield self.SMPPClientManagerPBProxy.start(self.c1.cid)

    @defer.inlineCallbacks
    def tearDown(self):
        # Stop smppc
        yield self.SMPPClientManagerPBProxy.stop(self.c1.cid)
        # Wait for 'BOUND_TRX' state
        while True:
            ssRet = yield self.SMPPClientManagerPBProxy.session_state(self.c1.cid)
            if ssRet == 'NONE' or ssRet == 'UNBOUND':
                break;
            else:
                yield waitFor(0.2)

        yield HappySMSCTestCase.tearDown(self)

class ProvisionInterceptorPB(ProvisionWithoutInterceptorPB):
    @defer.inlineCallbacks
    def setUp(self, authentication = False):
        "This will launch InterceptorPB and provide a client connected to it."
        # Launch a client in a disconnected state
        # it will be connected on demand through the self.ipb_connect() method
        self.ipb_client = InterceptorPBProxy()

        yield ProvisionWithoutInterceptorPB.setUp(self)

        # Initiating config objects without any filename
        # will lead to setting defaults and that's what we
        # need to run the tests
        InterceptorPBConfigInstance = InterceptorPBConfig()

        # Launch the interceptor server
        pbInterceptor_factory = InterceptorPB()
        pbInterceptor_factory.setConfig(InterceptorPBConfigInstance)

        # Configure portal
        p = portal.Portal(JasminPBRealm(pbInterceptor_factory))
        if not authentication:
            p.registerChecker(AllowAnonymousAccess())
        else:
            c = InMemoryUsernamePasswordDatabaseDontUse()
            c.addUser('test_user', md5('test_password').digest())
            p.registerChecker(c)
        jPBPortalRoot = JasminPBPortalRoot(p)
        self.pbInterceptor_server = reactor.listenTCP(0, pb.PBServerFactory(jPBPortalRoot))
        self.pbInterceptor_port = self.pbInterceptor_server.getHost().port

    @defer.inlineCallbacks
    def ipb_connect(self, config = None):
        if config is None:
            # Default test config (username is None for anonymous connection)
            config = InterceptorPBClientConfig()
            config.username = None
            config.port = self.pbInterceptor_port

        if config.username is not None:
            yield self.ipb_client.connect(
                config.host,
                config.port,
                config.username,
                config.password
            )
        else:
            yield self.ipb_client.connect(
                config.host,
                config.port
            )

    @defer.inlineCallbacks
    def tearDown(self):
        yield ProvisionWithoutInterceptorPB.tearDown(self)

        # Disconnect ipb and shutdown pbInterceptor_server
        if self.ipb_client.isConnected:
            self.ipb_client.disconnect()
        yield self.pbInterceptor_server.stopListening()

class HttpAPISubmitSmNoInterceptorPBTestCases(ProvisionWithoutInterceptorPB, RouterPBProxy, HappySMSCTestCase):

    @defer.inlineCallbacks
    def test_httpapi_send_interceptorpb_not_set(self):
        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/send?to=98700177&content=test&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since no interceptorpb is set
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '503')
        self.assertEqual(lastResponse, 'Error "InterceptorPB not set !"')

    @defer.inlineCallbacks
    def test_httpapi_rate_interceptorpb_not_set(self):
        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/rate?to=98700177&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since no interceptorpb is set
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '503')
        self.assertEqual(lastResponse, '"InterceptorPB not set !"')

class HttpAPISubmitSmInterceptionTestCases(ProvisionInterceptorPB, RouterPBProxy, HappySMSCTestCase):
    update_message_sript = "routable.pdu.params['short_message'] = 'Intercepted message'"

    @defer.inlineCallbacks
    def test_httpapi_send_interceptorpb_not_connected(self):
        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/send?to=98700177&content=test&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '503')
        self.assertEqual(lastResponse, 'Error "InterceptorPB not connected !"')

    @defer.inlineCallbacks
    def test_httpapi_send_interceptorpb_syntax_error(self):
        # Connect to InterceptorPB
        yield self.ipb_connect()

        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/send?to=98700177&content=test&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '400')
        self.assertEqual(lastResponse, 'Error "Failed running interception script, check log for details"')

    @defer.inlineCallbacks
    def test_httpapi_send_interceptorpb_success(self):
        # Re-provision interceptor with correct script
        mt_interceptor = MTInterceptorScript(self.update_message_sript)
        yield self.mtinterceptor_add(DefaultInterceptor(mt_interceptor), 0)

        # Connect to InterceptorPB
        yield self.ipb_connect()

        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/send?to=98700177&content=test&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Wait some time for message delivery through smppc
        yield waitFor(2)

        # Asserts
        self.assertEqual(lastErrorStatus, None)
        self.assertEqual(1, len(self.SMSCPort.factory.lastClient.submitRecords))
        self.assertEqual('Intercepted message', self.SMSCPort.factory.lastClient.submitRecords[0].params['short_message'])

    @defer.inlineCallbacks
    def test_httpapi_rate_interceptorpb_not_connected(self):
        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/rate?to=98700177&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '503')
        self.assertEqual(lastResponse, '"InterceptorPB not connected !"')

    @defer.inlineCallbacks
    def test_httpapi_rate_interceptorpb_syntax_error(self):
        # Connect to InterceptorPB
        yield self.ipb_connect()

        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/rate?to=98700177&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, '400')
        self.assertEqual(lastResponse, '"Failed running interception script, check log for details"')

    @defer.inlineCallbacks
    def test_httpapi_rate_interceptorpb_success(self):
        # Re-provision interceptor with correct script
        mt_interceptor = MTInterceptorScript(self.update_message_sript)
        yield self.mtinterceptor_add(DefaultInterceptor(mt_interceptor), 0)

        # Connect to InterceptorPB
        yield self.ipb_connect()

        # Send a SMS MT through http interface
        url = 'http://127.0.0.1:1401/rate?to=98700177&username=%s&password=%s' % (
            self.u1.username, self.u1_password)

        # We should receive an error since interceptorpb is not connected
        lastErrorStatus = None
        lastResponse = None
        try:
            yield getPage(url)
        except Exception, e:
            lastErrorStatus = e.status
            lastResponse = e.response

        # Asserts
        self.assertEqual(lastErrorStatus, None)
