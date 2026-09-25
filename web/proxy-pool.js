(() => {
  'use strict';

  const proxyFields = [
    ['proxy_mode','select',['auto','direct','single','pool']],
    ['proxy','text','full'],
    ['proxy_fallback','select',['none','direct','single']],
    ['proxy_pool_endpoint_mode','select',['auto','fixed','rotating']],
    ['proxy_pool_file','text','full'],
    ['proxy_pool_subscription_url','text','full'],
    ['proxy_pool_subscription_proxy','text','full'],
    ['proxy_pool_refresh_interval_sec','number',{min:0,max:86400}],
    ['proxy_pool_probe_interval_sec','number',{min:0,max:86400}],
    ['proxy_pool_probe_timeout_sec','number',{min:3,max:120}],
    ['proxy_pool_probe_provider','select',['cloudflare','ipinfo']],
    ['proxy_pool_probe_dual_stack','checkbox'],
    ['proxy_pool_max_concurrent_per_node','number',{min:1,max:64}],
    ['proxy_pool_acquire_timeout_sec','number',{min:1,max:600}],
    ['proxy_protocol_backend','select',['auto','sing-box','native-only']],
    ['proxy_singbox_path','text','full'],
    ['proxy_protocol_start_timeout_sec','number',{min:3,max:60}],
    ['proxy_runtime_idle_ttl_sec','number',{min:0,max:3600}],
    ['proxy_runtime_cache_max','number',{min:1,max:256}],
    ['proxy_pool_persist_health','checkbox'],
    ['proxy_pool_state_file','text','full'],
    ['proxy_pool_subscription_public_only','checkbox'],
    ['proxy_pool_preflight_enabled','checkbox'],
    ['us_consistency_enabled','checkbox'],
    ['us_consistency_timezone','text'],
    ['us_consistency_locale','text'],
    ['us_consistency_expect_country','text'],
    ['browser_path','text','full'],
    ['mooproxy_api','text','full'],
    ['mooproxy_country','text'],
    ['mooproxy_state','text'],
    ['mooproxy_via','text','full'],
    ['mooproxy_bridge_port','number',{min:1,max:65535}],
    ['lajiao_api_base','text','full'],
    ['lajiao_regions','text'],
    ['lajiao_num','number',{min:1,max:100}],
    ['lajiao_sticky_minutes','number',{min:1,max:120}],
    ['lajiao_extract_via','text','full'],
    ['lajiao_require_residential','checkbox'],
    ['lajiao_require_country','text'],
    ['cloudflare_fixed_address','text','full'],
    ['cloudflare_fixed_jwt','text','full'],
  ];

  const zh = {
    tabProxy:'代理池', proxyReload:'重新加载', proxyTest:'测试节点', proxyStatus:'代理节点状态',
    cpaStatus:'CPA 凭据', cpaRefresh:'刷新', cpaEmail:'邮箱', cpaExpired:'有效期至', cpaFile:'文件',
    cpaNone:'暂无已导出的凭据', cpaSyncOn:'远程同步', cpaSyncOff:'未启用远程同步',
    cpaSyncOk:'目标可达', cpaSyncFail:'目标不可达', cpaFailed:'导出失败', cpaCount:'个凭据',
    trafficTitle:'本批代理流量', trafficRefresh:'刷新', trafficStarted:'开始时间',
    trafficUp:'上行', trafficDown:'下行', trafficTotal:'合计', trafficAccounts:'成功账号',
    trafficNone:'本批暂无流量记录', trafficAvgBatch:'历史批次均值', trafficAvgAccount:'每账号均值',
    proxyEmpty:'暂无代理节点', proxyNode:'节点', proxyRunHealth:'运行健康', proxyProbeStatus:'探测状态',
    proxyLatency:'探测延迟', proxyExitIP:'出口 IP', proxyInflight:'占用', proxyFailures:'失败',
    proxyCooldown:'冷却', proxyType:'类型', proxyProtocol:'协议', proxyBackend:'后端',
    proxySourceSummary:'订阅解析', proxyError:'最近错误', probeHealthy:'正常', probeUnhealthy:'异常',
    probeUnknown:'未探测', probeUnavailable:'运行时不可用', noBusinessSamples:'未产生业务样本', failedAfter:'后失败',
    proxyIPv4:'IPv4', proxyIPv6:'IPv6', proxyGatewayRate:'出口成功率', proxySamples:'样本',
  };
  const en = {
    tabProxy:'Proxy pool', proxyReload:'Reload', proxyTest:'Test nodes', proxyStatus:'Proxy node status',
    cpaStatus:'CPA credentials', cpaRefresh:'Refresh', cpaEmail:'Email', cpaExpired:'Expires', cpaFile:'File',
    cpaNone:'No exported credentials yet', cpaSyncOn:'Remote sync', cpaSyncOff:'Remote sync disabled',
    cpaSyncOk:'target reachable', cpaSyncFail:'target unreachable', cpaFailed:'Export failed', cpaCount:'files',
    trafficTitle:'Batch proxy traffic', trafficRefresh:'Refresh', trafficStarted:'Started',
    trafficUp:'Up', trafficDown:'Down', trafficTotal:'Total', trafficAccounts:'Accounts',
    trafficNone:'No traffic recorded for this batch', trafficAvgBatch:'Avg per batch', trafficAvgAccount:'Avg per account',
    proxyEmpty:'No proxy nodes', proxyNode:'Node', proxyRunHealth:'Runtime health', proxyProbeStatus:'Probe status',
    proxyLatency:'Probe latency', proxyExitIP:'Exit IP', proxyInflight:'Inflight', proxyFailures:'Failures',
    proxyCooldown:'Cooldown', proxyType:'Type', proxyProtocol:'Protocol', proxyBackend:'Backend',
    proxySourceSummary:'Subscription parse', proxyError:'Last error', probeHealthy:'Healthy', probeUnhealthy:'Unhealthy',
    probeUnknown:'Not probed', probeUnavailable:'Runtime unavailable', noBusinessSamples:'No business samples', failedAfter:'to failure',
    proxyIPv4:'IPv4', proxyIPv6:'IPv6', proxyGatewayRate:'Exit success', proxySamples:'Samples',
  };
  Object.assign(i18n.zh, zh); Object.assign(i18n.en, en);
  Object.assign(i18n.zh.fields, {
    proxy_mode:['代理模式','auto 保持旧配置兼容；single/pool 启用账号级代理租约。'],
    proxy:['固定代理 / 单代理','auto 兼容旧代理；single 模式或 single fallback 使用。'],
    proxy_fallback:['代理池回退','none / direct / single。只在新账号租约获取前回退。'],
    proxy_pool_endpoint_mode:['节点类型','auto 会将含 {account} 的原生代理地址视为旋转代理入口。'],
    proxy_pool_file:['代理池文件','支持 HTTP/HTTPS/SOCKS/VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks；也支持 Base64 订阅文本。'],
    proxy_pool_subscription_url:['代理订阅 URL','支持普通文本或整份 Base64 编码的多协议节点订阅；刷新失败保留最近一次成功节点。'],
    proxy_pool_subscription_proxy:['订阅拉取代理','仅用于下载代理订阅，只接受 HTTP/HTTPS/SOCKS，可留空。'],
    proxy_pool_refresh_interval_sec:['订阅刷新间隔（秒）','0 表示关闭自动刷新。'],
    proxy_pool_probe_interval_sec:['健康探测间隔（秒）','0 表示关闭定期探测。探测状态与运行健康分相互独立。'],
    proxy_pool_probe_timeout_sec:['探测超时（秒）','单节点连通性检查超时。'],
    proxy_pool_probe_provider:['探测服务','用于验证代理连通性和出口 IP。'],
    proxy_pool_probe_dual_stack:['双栈探测','分别执行 IPv4 / IPv6 连通性探测。'],
    proxy_pool_max_concurrent_per_node:['单节点最大并发','默认 1，避免多个注册 Session 共用同一固定出口。'],
    proxy_pool_acquire_timeout_sec:['租约等待超时（秒）','代理被占用或冷却时等待可用节点的最长时间。'],
    proxy_protocol_backend:['高级协议后端','auto：原生 HTTP/SOCKS 通过统一 HTTP endpoint 使用，高级协议自动通过 sing-box；native-only 禁用高级协议。'],
    proxy_singbox_path:['sing-box 路径','留空时从 PATH 自动寻找 sing-box；VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks 使用。'],
    proxy_protocol_start_timeout_sec:['高级协议启动超时（秒）','等待本地 sing-box HTTP 出口就绪的最长时间。'],
    proxy_runtime_idle_ttl_sec:['运行时空闲缓存（秒）','引用数归零后继续保留一段时间，避免重复启动 bridge / sing-box；0 表示立即关闭。'],
    proxy_runtime_cache_max:['运行时缓存上限','空闲运行时超过上限时优先清理最久未使用项。'],
    proxy_pool_persist_health:['持久化代理健康','把固定节点的业务健康统计保存到本地 JSON。'],
    proxy_pool_state_file:['健康状态文件','仅在启用健康持久化时使用。'],
    proxy_pool_subscription_public_only:['订阅仅允许公网','启用后拒绝解析到私网/回环/保留地址的订阅 URL 和重定向。'],
    proxy_pool_preflight_enabled:['注册路径预检','保留非破坏性的 accounts.x.ai / grok.com 可达性预检能力。'],
    us_consistency_enabled:['美国环境一致性','统一浏览器时区/语言/platform，消除"IP=US 但时区=UTC、platform=Linux"这类自相矛盾特征。关闭即退回原版行为。'],
    us_consistency_timezone:['浏览器时区','必须与住宅 IP 所在国家一致。美国东部填 America/New_York，西部填 America/Los_Angeles。'],
    us_consistency_locale:['浏览器语言','需与出口国家匹配，美国用 en-US。'],
    us_consistency_expect_country:['代理出口期望国家','启动浏览器前探测代理真实出口，只有国家匹配时才按落地州对齐时区；不匹配则保持原时区，避免"将错就错"。'],
    browser_path:['Chromium 路径','留空自动探测（读 GROK_BROWSER_PATH / PLAYWRIGHT_BROWSERS_PATH 环境变量及常见安装位置）。容器内浏览器装在非标准目录时必须显式指定。'],
    mooproxy_api:['MooProxy 生成接口','住宅代理节点生成 API。站点在 Cloudflare 后，需带浏览器 UA 才能访问。'],
    mooproxy_country:['MooProxy 国家','目标国家代码，美国填 US。'],
    mooproxy_state:['MooProxy 州/省','仅用于构造请求；实测服务端不按此分配，实际落地州随机，时区由出口探测自动对齐。'],
    mooproxy_via:['MooProxy 中转代理','入口从本机直连时握手无响应，必须经干净出口中转，例如 socks5h://127.0.0.1:1080。'],
    mooproxy_bridge_port:['MooProxy 本地桥端口','链式桥监听端口，浏览器连本地端口再经中转连到 MooProxy。'],
    lajiao_api_base:['辣椒HTTP 提取接口','动态住宅代理的 IP 提取 API 地址。'],
    lajiao_regions:['辣椒提取地区','目标地区代码，美国填 US。'],
    lajiao_num:['辣椒单次提取数','建议与 register_count 匹配：每个账号独占一个 IP。'],
    lajiao_sticky_minutes:['辣椒粘性时长（分钟）','粘性会话保持时间，必须大于单账号完整流程耗时（注册+CPA），否则 IP 会在中途变化。'],
    lajiao_extract_via:['辣椒提取上游代理','调用提取接口时使用的上游代理，用于固定白名单源 IP（本机出口可能漂移）。'],
    lajiao_require_residential:['仅接受住宅 IP','入池前探测 hosting 标记，剔除机房 IP。'],
    lajiao_require_country:['强制出口国家','校验提取到的节点必须属于该国家，例如 US。'],
    cloudflare_fixed_address:['固定邮箱地址','留空则每个账号自动新建地址。填写后复用该地址，适用于实例已关闭建址的场景。'],
    cloudflare_fixed_jwt:['固定邮箱 JWT','与固定邮箱地址配套的地址级凭证（网页链接里 ?jwt= 后面那串）。'],
  });
  Object.assign(i18n.en.fields, {
    proxy_mode:['Proxy mode','auto preserves legacy behavior; single/pool enables account-scoped leases.'],
    proxy:['Fixed / single proxy','Used by legacy auto mode, single mode, or single fallback.'],
    proxy_fallback:['Pool fallback','none / direct / single; applied only before a new account lease starts.'],
    proxy_pool_endpoint_mode:['Endpoint type','auto treats native URLs containing {account} as rotating gateways.'],
    proxy_pool_file:['Proxy pool file','Supports HTTP/HTTPS/SOCKS/VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks and Base64 subscription text.'],
    proxy_pool_subscription_url:['Subscription URL','Plain/Base64 multi-protocol source; failed refreshes retain last-known-good nodes.'],
    proxy_pool_subscription_proxy:['Subscription fetch proxy','Used only to download the subscription; HTTP/HTTPS/SOCKS only.'],
    proxy_pool_refresh_interval_sec:['Refresh interval (seconds)','0 disables automatic source refresh.'],
    proxy_pool_probe_interval_sec:['Probe interval (seconds)','0 disables periodic probes. Probe status is independent from runtime health.'],
    proxy_pool_probe_timeout_sec:['Probe timeout (seconds)','Timeout for one connectivity probe.'],
    proxy_pool_probe_provider:['Probe provider','Used to verify connectivity and exit IP.'],
    proxy_pool_probe_dual_stack:['Dual-stack probe','Probe IPv4 and IPv6 connectivity independently.'],
    proxy_pool_max_concurrent_per_node:['Max sessions per node','Defaults to 1 to avoid sharing one fixed exit across account sessions.'],
    proxy_pool_acquire_timeout_sec:['Lease wait timeout (seconds)','Maximum wait while nodes are busy or cooling down.'],
    proxy_protocol_backend:['Advanced protocol backend','auto normalizes native proxies and routes advanced protocols through sing-box; native-only disables advanced protocols.'],
    proxy_singbox_path:['sing-box path','Leave blank to resolve from PATH; used by VLESS/VMess/Trojan/Hysteria2/TUIC/Shadowsocks.'],
    proxy_protocol_start_timeout_sec:['Advanced protocol startup timeout','Maximum wait for the local sing-box HTTP endpoint to become ready.'],
    proxy_runtime_idle_ttl_sec:['Runtime idle TTL','Keep idle bridge/sing-box runtimes for reuse; 0 closes immediately.'],
    proxy_runtime_cache_max:['Runtime cache limit','Evict oldest idle runtimes after this limit.'],
    proxy_pool_persist_health:['Persist proxy health','Persist fixed-node business health to local JSON.'],
    proxy_pool_state_file:['Health state file','Used only when health persistence is enabled.'],
    proxy_pool_subscription_public_only:['Public-only subscription','Reject subscription URLs/redirects resolving to private, loopback or reserved addresses.'],
    proxy_pool_preflight_enabled:['Registration preflight','Keep non-destructive accounts.x.ai / grok.com path preflight available.'],
    us_consistency_enabled:['US environment consistency','Normalize browser timezone/locale/platform so that IP=US no longer contradicts timezone=UTC or platform=Linux. Disable to restore upstream behavior.'],
    us_consistency_timezone:['Browser timezone','Must match the residential IP country. Use America/New_York for US East, America/Los_Angeles for US West.'],
    us_consistency_locale:['Browser locale','Should match the exit country; en-US for the United States.'],
    us_consistency_expect_country:['Expected exit country','Probes the real proxy exit before launching the browser and aligns the timezone to the actual region only when the country matches; otherwise the timezone is left untouched.'],
    browser_path:['Chromium path','Leave empty to auto-detect (via GROK_BROWSER_PATH / PLAYWRIGHT_BROWSERS_PATH and common install locations). Required when the browser lives outside standard paths, e.g. inside a container.'],
    mooproxy_api:['MooProxy generate API','Residential proxy node generation endpoint. The site sits behind Cloudflare, so a browser User-Agent is required.'],
    mooproxy_country:['MooProxy country','Target country code; use US for the United States.'],
    mooproxy_state:['MooProxy state','Only used to build the request; the server does not honour it in practice, so the actual region is random and the timezone is aligned from the exit probe.'],
    mooproxy_via:['MooProxy relay proxy','The endpoint does not complete a handshake when dialled directly from this host; relay through a clean exit such as socks5h://127.0.0.1:1080.'],
    mooproxy_bridge_port:['MooProxy bridge port','Local chained-bridge port; the browser connects locally and traffic is relayed on to MooProxy.'],
    lajiao_api_base:['Lajiao extract API','IP extraction endpoint for the dynamic residential proxy.'],
    lajiao_regions:['Lajiao region','Target region code, e.g. US.'],
    lajiao_num:['Lajiao batch size','Keep aligned with register_count: one dedicated IP per account.'],
    lajiao_sticky_minutes:['Lajiao sticky minutes','Sticky session duration; must exceed a full account flow (registration + CPA) or the IP changes mid-flow.'],
    lajiao_extract_via:['Lajiao extract upstream','Upstream proxy used when calling the extract API, to pin a whitelisted source IP (this host may egress from multiple IPs).'],
    lajiao_require_residential:['Residential only','Probe the hosting flag and drop datacenter IPs before they enter the pool.'],
    lajiao_require_country:['Enforce exit country','Reject extracted nodes that do not belong to this country, e.g. US.'],
    cloudflare_fixed_address:['Fixed mail address','Leave empty to create a fresh address per account. Set it to reuse one address, e.g. when address creation is disabled on the instance.'],
    cloudflare_fixed_jwt:['Fixed mail JWT','Address-level credential paired with the fixed address (the ?jwt= value in the web UI URL).'],
  });

  icons.proxy = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 11l8-4M8 13l8 4"/></svg>';
  fieldDefs.basic = fieldDefs.basic.filter(([key]) => key !== 'proxy');
  fieldDefs.proxy = proxyFields;
  tabMeta.proxy = ['tabProxy','proxy'];
  renderFields();
  applyLanguage();

  const proxyTab = document.querySelector('[data-tabkey="tabProxy"]');
  const basicTab = document.querySelector('[data-tabkey="tabBasic"]');
  if (proxyTab && basicTab && basicTab.nextSibling !== proxyTab) basicTab.after(proxyTab);
  const proxySection = document.getElementById('sec-proxy');
  const basicSection = document.getElementById('sec-basic');
  if (proxySection && basicSection && basicSection.nextSibling !== proxySection) basicSection.after(proxySection);

  if (proxySection) {
    const shell = document.createElement('div');
    shell.className = 'proxy-status-shell';
    shell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="proxyStatus">${t('proxyStatus')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="proxyReloadBtn" class="mini-btn"><span data-i18n="proxyReload">${t('proxyReload')}</span></button>
          <button type="button" id="proxyTestBtn" class="mini-btn"><span data-i18n="proxyTest">${t('proxyTest')}</span></button>
        </div>
      </div>
      <div id="proxyPoolSummary" class="proxy-summary"></div>
      <div id="proxySourceSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table"><thead><tr>
        <th data-i18n="proxyNode">${t('proxyNode')}</th><th data-i18n="proxyProtocol">${t('proxyProtocol')}</th>
        <th data-i18n="proxyBackend">${t('proxyBackend')}</th><th data-i18n="proxyType">${t('proxyType')}</th>
        <th data-i18n="proxyProbeStatus">${t('proxyProbeStatus')}</th><th data-i18n="proxyRunHealth">${t('proxyRunHealth')}</th>
        <th data-i18n="proxyLatency">${t('proxyLatency')}</th><th data-i18n="proxyExitIP">${t('proxyExitIP')}</th>
        <th data-i18n="proxyInflight">${t('proxyInflight')}</th><th data-i18n="proxyFailures">${t('proxyFailures')}</th>
        <th data-i18n="proxyCooldown">${t('proxyCooldown')}</th><th data-i18n="proxyError">${t('proxyError')}</th>
      </tr></thead><tbody id="proxyPoolRows"></tbody></table></div>`;
    proxySection.appendChild(shell);
  }

  // CPA 凭据状态：本地导出清单 + 远程同步可达性。
  if (proxySection) {
    const cpaShell = document.createElement('div');
    cpaShell.className = 'proxy-status-shell';
    cpaShell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="cpaStatus">${t('cpaStatus')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="cpaRefreshBtn" class="mini-btn"><span data-i18n="cpaRefresh">${t('cpaRefresh')}</span></button>
        </div>
      </div>
      <div id="cpaSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table"><thead><tr>
        <th data-i18n="cpaEmail">${t('cpaEmail')}</th><th data-i18n="cpaExpired">${t('cpaExpired')}</th>
        <th data-i18n="cpaFile">${t('cpaFile')}</th>
      </tr></thead><tbody id="cpaRows"></tbody></table></div>`;
    proxySection.appendChild(cpaShell);
  }

  // 本批代理流量：住宅代理按流量计费，这里直接显示用量与历史均值。
  if (proxySection) {
    const trafficShell = document.createElement('div');
    trafficShell.className = 'proxy-status-shell';
    trafficShell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="trafficTitle">${t('trafficTitle')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="trafficRefreshBtn" class="mini-btn"><span data-i18n="trafficRefresh">${t('trafficRefresh')}</span></button>
        </div>
      </div>
      <div id="trafficSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table"><thead><tr>
        <th data-i18n="trafficStarted">${t('trafficStarted')}</th><th data-i18n="trafficUp">${t('trafficUp')}</th>
        <th data-i18n="trafficDown">${t('trafficDown')}</th><th data-i18n="trafficTotal">${t('trafficTotal')}</th>
        <th data-i18n="trafficAccounts">${t('trafficAccounts')}</th>
      </tr></thead><tbody id="trafficRows"></tbody></table></div>`;
    proxySection.appendChild(trafficShell);
  }

  function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function renderSourceSummary(data) {
    const target = document.getElementById('proxySourceSummary'); if (!target) return;
    const sources = data && data.sources && typeof data.sources === 'object' ? data.sources : {}; const parts = [];
    for (const key of ['subscription','file']) {
      const source = sources[key]; if (!source || typeof source !== 'object') continue;
      const counts = source.protocol_counts || {}; const protocolText = Object.entries(counts).map(([name,count]) => `${name}:${count}`).join(' · ');
      parts.push(`${key}: ${source.supported || 0}/${source.total_lines || 0}${source.decoded_base64 ? ' · Base64' : ''}${protocolText ? ' · '+protocolText : ''}${source.skipped ? ' · skipped:'+source.skipped : ''}${source.stale ? ' · LKG(stale)' : ''}${source.error ? ' · '+source.error : ''}`);
    }
    target.textContent = parts.join(' || ');
  }
  function probeText(status) {
    if (status === 'healthy') return t('probeHealthy'); if (status === 'unhealthy') return t('probeUnhealthy');
    if (status === 'unavailable') return t('probeUnavailable'); return t('probeUnknown');
  }
  function familyText(node, key) {
    const p = node[key] || {}; if (!p.status || p.status === 'unknown') return `${key === 'ipv4_probe' ? 'IPv4' : 'IPv6'} —`;
    return `${key === 'ipv4_probe' ? 'IPv4' : 'IPv6'} ${probeText(p.status)}${p.latency_ms ? ' '+p.latency_ms+'ms' : ''}${p.exit_ip ? ' '+p.exit_ip : ''}`;
  }
  function renderProxyStatus(data) {
    const rows = document.getElementById('proxyPoolRows'); const summary = document.getElementById('proxyPoolSummary'); if (!rows || !summary) return;
    const nodes = Array.isArray(data.nodes) ? data.nodes : []; summary.textContent = `${data.mode || 'auto'} · ${nodes.length} nodes${data.persist_health ? ' · persisted health' : ''}`; renderSourceSummary(data);
    if (!nodes.length) { rows.innerHTML = `<tr><td colspan="12" class="proxy-empty">${esc(t('proxyEmpty'))}</td></tr>`; return; }
    rows.innerHTML = nodes.map(node => {
      const status = node.probe_status === 'healthy' ? 'good' : (node.probe_status === 'unhealthy' || node.probe_status === 'unavailable') ? 'bad' : '';
      const label = node.name ? `${node.name} · ${node.proxy}` : node.proxy; const samples = Number(node.business_samples || 0);
      const health = node.rotating
        ? `${t('proxyGatewayRate')}: ${node.gateway_success_rate == null ? '—' : Math.round(Number(node.gateway_success_rate)*1000)/10+'%'} · exits=${Number(node.exit_successes||0)+Number(node.exit_failures||0)}`
        : (samples > 0 ? `${node.health} · n=${samples}` : `— · ${t('noBusinessSamples')}`);
      const latency = `${familyText(node,'ipv4_probe')} / ${familyText(node,'ipv6_probe')}`;
      const error = node.probe_error || node.last_error || '—';
      const failures = node.rotating ? `${node.exit_failures || 0} exits` : `${node.failure_count || 0} · transport=${node.transport_failures || 0} · config=${node.configuration_failures || 0}`;
      return `<tr>
        <td title="${esc(node.id)}"><span class="proxy-dot ${status}"></span>${esc(label)}</td>
        <td>${esc(node.protocol || '—')}</td><td>${esc(node.backend || 'native')}</td>
        <td>${node.rotating ? 'rotating gateway' : 'fixed'}</td><td>${esc(probeText(node.probe_status))}</td>
        <td>${esc(health)}</td><td>${esc(latency)}</td><td>${esc(node.exit_ip || '—')}</td>
        <td>${esc(node.inflight)}</td><td>${esc(failures)}</td><td>${node.rotating ? 'N/A' : (node.cooldown_sec ? esc(node.cooldown_sec)+' s' : '—')}</td>
        <td title="${esc(error)}">${esc(error)}</td>
      </tr>`;
    }).join('');
  }
  async function refreshProxyStatus() { try { const r = await fetch('./api/proxy-pool/status'); if (!r.ok) return; renderProxyStatus(await r.json()); } catch (_) {} }

  function renderCpaStatus(data) {
    const rows = document.getElementById('cpaRows');
    const summary = document.getElementById('cpaSummary');
    if (!rows || !summary) return;
    const items = Array.isArray(data.credentials) ? data.credentials : [];
    const failed = Array.isArray(data.failed) ? data.failed : [];
    const sync = data.sync || {};
    const parts = [];
    parts.push(`${data.count || 0} ${t('cpaCount')}`);
    if (!data.export_enabled) parts.push('⚠️ export disabled');
    if (sync.enabled) {
      const state = sync.reachable === true ? t('cpaSyncOk') : (sync.reachable === false ? t('cpaSyncFail') : '—');
      parts.push(`${t('cpaSyncOn')}: ${sync.target || '—'} · ${state}${sync.error ? ' · ' + sync.error : ''}`);
    } else {
      parts.push(t('cpaSyncOff'));
    }
    if (failed.length) parts.push(`${t('cpaFailed')}: ${failed.length}`);
    summary.textContent = parts.join(' · ');
    if (!items.length) {
      rows.innerHTML = `<tr><td colspan="3" class="proxy-empty">${esc(t('cpaNone'))}</td></tr>`;
      return;
    }
    rows.innerHTML = items.map(item => `<tr>
      <td>${esc(item.email || '—')}</td>
      <td>${esc(item.expired || '—')}</td>
      <td title="${esc(item.file)}">${esc(item.file)}</td>
    </tr>`).join('');
  }
  async function refreshCpaStatus() {
    try {
      const r = await fetch('./api/cpa/status');
      if (!r.ok) return;
      renderCpaStatus(await r.json());
    } catch (_) {}
  }

  function renderTraffic(data) {
    const rows = document.getElementById('trafficRows');
    const summary = document.getElementById('trafficSummary');
    if (!rows || !summary) return;
    const cur = data.current || {};
    const parts = [];
    parts.push(`${t('trafficTotal')}: ${cur.bytes_total_text || '—'}`);
    if (data.average_batch) parts.push(`${t('trafficAvgBatch')}: ${data.average_batch_text}`);
    if (data.average_account) parts.push(`${t('trafficAvgAccount')}: ${data.average_account_text}`);
    summary.textContent = parts.join(' · ');
    const history = Array.isArray(data.history) ? data.history : [];
    if (!history.length) {
      rows.innerHTML = `<tr><td colspan="5" class="proxy-empty">${esc(t('trafficNone'))}</td></tr>`;
      return;
    }
    rows.innerHTML = history.map(item => `<tr>
      <td>${esc(item.started_at || '—')}</td>
      <td>${esc(formatBytes(item.bytes_up))}</td>
      <td>${esc(formatBytes(item.bytes_down))}</td>
      <td>${esc(item.bytes_total_text || '—')}</td>
      <td>${esc(item.accounts == null ? '—' : item.accounts)}</td>
    </tr>`).join('');
  }
  function formatBytes(value) {
    const n = Number(value || 0);
    if (!isFinite(n) || n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = n, i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
    return `${i === 0 ? Math.round(size) : size.toFixed(2)} ${units[i]}`;
  }
  async function refreshTraffic() {
    try {
      const r = await fetch('./api/traffic');
      if (!r.ok) return;
      renderTraffic(await r.json());
    } catch (_) {}
  }

  async function proxyAction(path) {
    if (dirty.size && !await saveConfig()) return;
    const reload = document.getElementById('proxyReloadBtn'); const test = document.getElementById('proxyTestBtn');
    if (reload) reload.disabled = true; if (test) test.disabled = true;
    try { const r = await fetch(path,{method:'POST'}); const d = await r.json(); if (!r.ok) setNotice(d.detail || 'Proxy pool operation failed', true); else { renderProxyStatus(d); setNotice(''); } }
    catch (e) { setNotice(e.message, true); }
    finally { if (reload) reload.disabled = !!running; if (test) test.disabled = !!running; }
  }
  const reloadBtn = document.getElementById('proxyReloadBtn'); const testBtn = document.getElementById('proxyTestBtn');
  if (reloadBtn) reloadBtn.onclick = () => proxyAction('./api/proxy-pool/reload');
  if (testBtn) testBtn.onclick = () => proxyAction('./api/proxy-pool/test');
  const cpaRefreshBtn = document.getElementById('cpaRefreshBtn');
  if (cpaRefreshBtn) cpaRefreshBtn.onclick = () => refreshCpaStatus();
  const trafficRefreshBtn = document.getElementById('trafficRefreshBtn');
  if (trafficRefreshBtn) trafficRefreshBtn.onclick = () => refreshTraffic();
  loadConfig().catch(e => setNotice(e.message,true)); refreshProxyStatus(); refreshCpaStatus(); refreshTraffic();
  setInterval(() => { if (reloadBtn) reloadBtn.disabled = !!running; if (testBtn) testBtn.disabled = !!running; refreshProxyStatus(); }, 2000);
  // CPA 状态含远程 SSH 探测，开销较大，用较低频率轮询。
  setInterval(refreshCpaStatus, 15000);
  setInterval(refreshTraffic, 5000);
})();
