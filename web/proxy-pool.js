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
    ['novproxy_api','text','full'],
    ['novproxy_region','text'],
    ['novproxy_minutes','number',{min:1,max:1440}],
    ['novproxy_num','number',{min:1,max:500}],
    ['quality_auto_probe','checkbox'],
    ['quality_soft_threshold','number',{min:1,max:5000}],
    ['browser_path','text','full'],
    ['cloudflare_fixed_address','text','full'],
    ['cloudflare_fixed_jwt','text','full'],
  ];

  const zh = {
    tabProxy:'代理池', proxyReload:'重新加载', proxyTest:'测试节点', proxyStatus:'代理节点状态',
    novproxyExtract:'提取 NovProxy 节点 (一号一IP)',
    cpaStatus:'CPA 凭据', cpaRefresh:'刷新', cpaEmail:'邮箱', cpaExpired:'有效期至', cpaFile:'文件',
    cpaNone:'暂无已导出的凭据', cpaBfs:'BFS 标记', cpaBfsYes:'已标记', cpaSyncOn:'远程同步', cpaSyncOff:'未启用远程同步',
    cpaSyncOk:'目标可达', cpaSyncFail:'目标不可达', cpaFailed:'导出失败', cpaCount:'个凭据',
    qualityScan:'降智质量扫描', qualityScanning:'扫描中...', cpaQuality:'降智检测',
    trafficTitle:'代理流量计量', trafficRefresh:'刷新', trafficStarted:'开始时间',
    trafficUp:'上行', trafficDown:'下行', trafficTotal:'合计', trafficAccounts:'成功账号',
    trafficNone:'暂无流量记录', trafficAvgBatch:'历史批次均值', trafficAvgAccount:'每账号均值',
    trafficLifetime:'历史总用量', trafficWindow1h:'近1h', trafficWindow24h:'近24h', trafficWindow7d:'近7d',
    proxyEmpty:'暂无代理节点', proxyNode:'节点', proxyRunHealth:'运行健康', proxyProbeStatus:'探测状态',
    proxyDetail:'详细', proxyStatusCol:'状态', proxyNodesUnit:'个节点', proxyUsable:'可用',
    proxyUnhealthy:'异常', proxyCooling:'冷却中', proxyRetired:'已退役', proxyDisabled:'已停用',
    proxyHealthy:'健康',
    proxyNodeUnit:'节点',
    proxyLatency:'探测延迟', proxyExitIP:'出口 IP', proxyInflight:'占用', proxyFailures:'失败',
    proxyCooldown:'冷却', proxyType:'类型', proxyProtocol:'协议', proxyBackend:'后端',
    proxySourceSummary:'订阅解析', proxyError:'最近错误', probeHealthy:'正常', probeUnhealthy:'异常',
    probeUnknown:'未探测', probeUnavailable:'运行时不可用', noBusinessSamples:'未产生业务样本', failedAfter:'后失败',
    proxyIPv4:'IPv4', proxyIPv6:'IPv6', proxyGatewayRate:'出口成功率', proxySamples:'样本',
  };
  const en = {
    tabProxy:'Proxy pool', proxyReload:'Reload', proxyTest:'Test nodes', proxyStatus:'Proxy node status',
    novproxyExtract:'Extract NovProxy (num=N)',
    cpaStatus:'CPA credentials', cpaRefresh:'Refresh', cpaEmail:'Email', cpaExpired:'Expires', cpaFile:'File',
    cpaNone:'No exported credentials yet', cpaBfs:'BFS flag', cpaBfsYes:'flagged', cpaSyncOn:'Remote sync', cpaSyncOff:'Remote sync disabled',
    cpaSyncOk:'target reachable', cpaSyncFail:'target unreachable', cpaFailed:'Export failed', cpaCount:'files',
    qualityScan:'Probe Quality', qualityScanning:'Probing...', cpaQuality:'Quality',
    trafficTitle:'Proxy Traffic Meter', trafficRefresh:'Refresh', trafficStarted:'Started',
    trafficUp:'Up', trafficDown:'Down', trafficTotal:'Total', trafficAccounts:'Accounts',
    trafficNone:'No traffic recorded', trafficAvgBatch:'Avg per batch', trafficAvgAccount:'Avg per account',
    trafficLifetime:'Lifetime Total', trafficWindow1h:'Past 1h', trafficWindow24h:'Past 24h', trafficWindow7d:'Past 7d',
    proxyEmpty:'No proxy nodes', proxyNode:'Node', proxyRunHealth:'Runtime health', proxyProbeStatus:'Probe status',
    proxyDetail:'Details', proxyStatusCol:'Status', proxyNodesUnit:'nodes', proxyUsable:'usable',
    proxyUnhealthy:'unhealthy', proxyCooling:'cooling', proxyRetired:'retired', proxyDisabled:'disabled',
    proxyHealthy:'healthy',
    proxyNodeUnit:'Node',
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
    novproxy_api:['NovProxy 提取接口','动态住宅代理 API 提取地址，返回格式为 host:port。'],
    novproxy_region:['NovProxy 地区代码','目标地区代码，默认 US。'],
    novproxy_minutes:['NovProxy 粘性时长（分钟）','住宅 IP 保持时间，默认 120 分钟（覆盖注册及 CPA 导出全流程）。'],
    novproxy_num:['NovProxy 提取数量 (num=N)','建议与注册数量匹配：一账号独占一个出口 IP，避免同 IP 关联。'],
    quality_auto_probe:['注册后自动降智测试','注册并导出 CPA 后自动发起流式质量探测，验证账号推理健康度。'],
    quality_soft_threshold:['降智测试可疑阈值','低于该推理 token 数量（默认 50）标记为可疑；0 标记为降智。'],
    browser_path:['Chromium 路径','留空自动探测（读 GROK_BROWSER_PATH / PLAYWRIGHT_BROWSERS_PATH 环境变量及常见安装位置）。容器内浏览器装在非标准目录时必须显式指定。'],
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
    novproxy_api:['NovProxy extract API','Residential proxy extract API endpoint, returns host:port lines.'],
    novproxy_region:['NovProxy region','Target region code, defaults to US.'],
    novproxy_minutes:['NovProxy sticky minutes','Sticky session duration, defaults to 120 minutes.'],
    novproxy_num:['NovProxy batch size (num=N)','Keep aligned with register count: one dedicated residential IP per account.'],
    quality_auto_probe:['Auto quality probe','Automatically probe reasoning tokens after account registration and CPA export.'],
    quality_soft_threshold:['Soft reasoning threshold','Reasoning tokens below this count (default 50) marked as soft/suspicious.'],
    browser_path:['Chromium path','Leave empty to auto-detect (via GROK_BROWSER_PATH / PLAYWRIGHT_BROWSERS_PATH and common install locations). Required when the browser lives outside standard paths, e.g. inside a container.'],
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
    // 默认只展示关键列。其余列（协议/后端/探针明细/失败分解等）绝大多数
    // 时候是空值或 0，堆在一起反而看不清状态，收进「详细」里按需展开。
    shell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="proxyStatus">${t('proxyStatus')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="novproxyExtractBtn" class="mini-btn"><span data-i18n="novproxyExtract">${t('novproxyExtract')}</span></button>
          <button type="button" id="proxyDetailBtn" class="mini-btn"><span data-i18n="proxyDetail">${t('proxyDetail')}</span></button>
          <button type="button" id="proxyReloadBtn" class="mini-btn"><span data-i18n="proxyReload">${t('proxyReload')}</span></button>
          <button type="button" id="proxyTestBtn" class="mini-btn"><span data-i18n="proxyTest">${t('proxyTest')}</span></button>
        </div>
      </div>
      <div id="proxyPoolSummary" class="proxy-summary"></div>
      <div id="proxySourceSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table" id="proxyPoolTable"><thead><tr>
        <th data-i18n="proxyNode">${t('proxyNode')}</th>
        <th data-i18n="proxyExitIP">${t('proxyExitIP')}</th>
        <th data-i18n="proxyRunHealth">${t('proxyRunHealth')}</th>
        <th data-i18n="proxyStatusCol">${t('proxyStatusCol')}</th>
        <th class="proxy-adv" data-i18n="proxyProtocol">${t('proxyProtocol')}</th>
        <th class="proxy-adv" data-i18n="proxyBackend">${t('proxyBackend')}</th>
        <th class="proxy-adv" data-i18n="proxyType">${t('proxyType')}</th>
        <th class="proxy-adv" data-i18n="proxyProbeStatus">${t('proxyProbeStatus')}</th>
        <th class="proxy-adv" data-i18n="proxyLatency">${t('proxyLatency')}</th>
        <th class="proxy-adv" data-i18n="proxyInflight">${t('proxyInflight')}</th>
        <th class="proxy-adv" data-i18n="proxyFailures">${t('proxyFailures')}</th>
        <th class="proxy-adv" data-i18n="proxyCooldown">${t('proxyCooldown')}</th>
        <th class="proxy-adv" data-i18n="proxyError">${t('proxyError')}</th>
      </tr></thead><tbody id="proxyPoolRows"></tbody></table></div>`;
    proxySection.appendChild(shell);
    const detailBtn = document.getElementById('proxyDetailBtn');
    if (detailBtn) detailBtn.onclick = () => {
      const table = document.getElementById('proxyPoolTable');
      if (!table) return;
      const on = table.classList.toggle('show-adv');
      detailBtn.classList.toggle('active', on);
    };
  }

  // CPA 凭据状态：本地导出清单 + 远程同步可达性。
  if (proxySection) {
    const cpaShell = document.createElement('div');
    cpaShell.className = 'proxy-status-shell';
    cpaShell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="cpaStatus">${t('cpaStatus')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="qualityScanBtn" class="mini-btn"><span data-i18n="qualityScan">${t('qualityScan')}</span></button>
          <button type="button" id="cpaRefreshBtn" class="mini-btn"><span data-i18n="cpaRefresh">${t('cpaRefresh')}</span></button>
        </div>
      </div>
      <div id="cpaSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table"><thead><tr>
        <th data-i18n="cpaEmail">${t('cpaEmail')}</th><th data-i18n="cpaExpired">${t('cpaExpired')}</th>
        <th data-i18n="cpaQuality">${t('cpaQuality')}</th>
        <th data-i18n="cpaBfs">${t('cpaBfs')}</th>
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
  // 代理地址里含明文账密，展示时必须遮罩。
  // 住宅代理节点靠 session-XXXX 之类的标识区分，把它提出来当标签最直观。
  function maskProxy(raw) {
    const text = String(raw || '');
    try {
      const m = text.match(/^([a-z0-9+.-]+:\/\/)([^@]*)@(.+)$/i);
      if (!m) return text;
      const user = (m[2].split(':')[0] || '').replace(/\/\/.*$/, '');
      return `${m[1]}${user ? user + ':***' : '***'}@${m[3]}`;
    } catch (_) {
      return text.replace(/\/\/[^@]*@/, '//***@');
    }
  }
  // session 串（如 NrQFyoku）对人不直观，改用序号做主标识，
  // 具体 session 放进 tooltip，需要排查时仍能看到。
  function nodeSession(node) {
    try {
      const cred = decodeURIComponent(String(node.proxy || '').split('@')[0] || '');
      const m = cred.match(/session-([A-Za-z0-9]+)/);
      return m ? m[1] : '';
    } catch (_) { return ''; }
  }
  function nodeLabel(node, index) {
    if (node.name) return node.name;
    return `${t('proxyNodeUnit')} ${index + 1}`;
  }
  function nodeTooltip(node, index) {
    const session = nodeSession(node);
    return [`${t('proxyNodeUnit')} ${index + 1}`, session ? `session: ${session}` : '', maskProxy(node.proxy)]
      .filter(Boolean).join('\n');
  }
  function nodeState(node) {
    // 把一堆布尔/计数压成一个可读状态
    if (node.retired) return { text: t('proxyRetired'), cls: 'bad' };
    if (!node.enabled) return { text: t('proxyDisabled'), cls: 'bad' };
    if (node.cooldown_sec) return { text: `${t('proxyCooling')} ${node.cooldown_sec}s`, cls: 'bad' };
    if (node.probe_status === 'unhealthy' || node.probe_status === 'unavailable') return { text: probeText(node.probe_status), cls: 'bad' };
    if (node.failure_count || node.transport_failures) return { text: `${t('proxyFailures')} ${node.failure_count || 0}`, cls: '' };
    if (node.probe_status === 'healthy') return { text: probeText('healthy'), cls: 'good' };
    return { text: probeText('unknown'), cls: '' };
  }
  function renderProxyStatus(data) {
    const rows = document.getElementById('proxyPoolRows'); const summary = document.getElementById('proxyPoolSummary'); if (!rows || !summary) return;
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    // 按真实状态计数。注意「可用」不能只看 enabled/retired —— 把从未探测过
    // 的节点算作可用会误导：实测有整批节点已失效，面板却仍显示全部可用。
    const healthy = nodes.filter(n => n.probe_status === 'healthy').length;
    const cooling = nodes.filter(n => n.cooldown_sec).length;
    const flagged = nodes.filter(n => n.probe_status === 'unhealthy' || n.probe_status === 'unavailable').length;
    const unprobed = nodes.filter(n => !n.probe_status || n.probe_status === 'unknown').length;
    const disabled = nodes.filter(n => !n.enabled || n.retired).length;
    const parts = [`${data.mode || 'auto'}`, `${nodes.length} ${t('proxyNodesUnit')}`];
    if (healthy) parts.push(`${healthy} ${t('proxyHealthy')}`);
    if (flagged) parts.push(`${flagged} ${t('proxyUnhealthy')}`);
    if (cooling) parts.push(`${cooling} ${t('proxyCooling')}`);
    if (disabled) parts.push(`${disabled} ${t('proxyDisabled')}`);
    if (unprobed) parts.push(`${unprobed} ${t('probeUnknown')}`);
    summary.textContent = parts.join(' · ');
    renderSourceSummary(data);
    if (!nodes.length) { rows.innerHTML = `<tr><td colspan="13" class="proxy-empty">${esc(t('proxyEmpty'))}</td></tr>`; return; }
    rows.innerHTML = nodes.map((node, index) => {
      const status = node.probe_status === 'healthy' ? 'good' : (node.probe_status === 'unhealthy' || node.probe_status === 'unavailable') ? 'bad' : '';
      const label = nodeLabel(node, index); const samples = Number(node.business_samples || 0);
      const health = node.rotating
        ? `${t('proxyGatewayRate')}: ${node.gateway_success_rate == null ? '—' : Math.round(Number(node.gateway_success_rate)*1000)/10+'%'} · exits=${Number(node.exit_successes||0)+Number(node.exit_failures||0)}`
        : (samples > 0 ? `${node.health} · n=${samples}` : `— · ${t('noBusinessSamples')}`);
      const latency = `${familyText(node,'ipv4_probe')} / ${familyText(node,'ipv6_probe')}`;
      const error = node.probe_error || node.last_error || '—';
      const failures = node.rotating ? `${node.exit_failures || 0} exits` : `${node.failure_count || 0} · transport=${node.transport_failures || 0} · config=${node.configuration_failures || 0}`;
      const state = nodeState(node);
      return `<tr>
        <td title="${esc(nodeTooltip(node, index))}"><span class="proxy-dot ${status}"></span>${esc(label)}</td>
        <td>${esc(node.exit_ip || '—')}</td>
        <td>${esc(health)}</td>
        <td><span class="proxy-state ${state.cls}">${esc(state.text)}</span></td>
        <td class="proxy-adv">${esc(node.protocol || '—')}</td>
        <td class="proxy-adv">${esc(node.backend || 'native')}</td>
        <td class="proxy-adv">${node.rotating ? 'rotating gateway' : 'fixed'}</td>
        <td class="proxy-adv">${esc(probeText(node.probe_status))}</td>
        <td class="proxy-adv">${esc(latency)}</td>
        <td class="proxy-adv">${esc(node.inflight)}</td>
        <td class="proxy-adv">${esc(failures)}</td>
        <td class="proxy-adv">${node.rotating ? 'N/A' : (node.cooldown_sec ? esc(node.cooldown_sec)+' s' : '—')}</td>
        <td class="proxy-adv" title="${esc(error)}">${esc(error)}</td>
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
    const qs = data.quality_summary || {};
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
    if (data.bfs_flagged) parts.push(`${t('cpaBfs')}: ${data.bfs_flagged}`);
    if (qs.total) {
      parts.push(`质量: ✅${qs.healthy || 0} · 🧠${qs.hard || 0} · ⚠️${qs.soft || 0} · 🚫${qs.risk || 0}`);
    }
    if (data.quality_scanning) {
      const st = data.quality_state || {};
      parts.push(`[${t('qualityScanning')} ${st.completed || 0}/${st.total || 0}]`);
    }
    summary.textContent = parts.join(' · ');
    if (!items.length) {
      rows.innerHTML = `<tr><td colspan="5" class="proxy-empty">${esc(t('cpaNone'))}</td></tr>`;
      return;
    }
    rows.innerHTML = items.map(item => {
      let qualityHtml = '<span style="color:var(--text-dim, #888)">—</span>';
      if (item.quality) {
        const v = item.quality.verdict;
        const tok = item.quality.reasoning_tokens || 0;
        if (v === 'healthy') {
          qualityHtml = `<span style="color:#10b981;font-weight:600">✅ 正常 (${tok} tok)</span>`;
        } else if (v === 'hard') {
          qualityHtml = `<span style="color:#ef4444;font-weight:600">🧠 降智 (0 tok)</span>`;
        } else if (v === 'soft') {
          qualityHtml = `<span style="color:#f59e0b;font-weight:600">⚠️ 可疑 (${tok} tok)</span>`;
        } else if (v === 'risk') {
          qualityHtml = `<span style="color:#f87171" title="${esc(item.quality.error || '')}">🚫 不可用</span>`;
        } else if (v === 'error') {
          qualityHtml = `<span style="color:#a8a29e" title="${esc(item.quality.error || '')}">❓ 出错</span>`;
        }
      }
      return `<tr>
      <td>${esc(item.email || '—')}</td>
      <td>${esc(item.expired || '—')}</td>
      <td>${qualityHtml}</td>
      <td>${item.bfs ? '<span class="proxy-dot bad"></span>' + esc(t('cpaBfsYes')) + (item.bfs_value != null ? ' (' + esc(item.bfs_value) + ')' : '') : '—'}</td>
      <td title="${esc(item.file)}">${esc(item.file)}</td>
    </tr>`;
    }).join('');
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
    const life = data.lifetime || {};
    const win = data.windows || {};
    const parts = [];
    parts.push(`本批: ${cur.bytes_total_text || '0 B'} (↑${formatBytes(cur.bytes_up)} · ↓${formatBytes(cur.bytes_down)})`);
    if (life && (life.bytes_total || life.bytes_total_text)) {
      parts.push(`${t('trafficLifetime')}: ${life.bytes_total_text || formatBytes(life.bytes_total)}`);
    }
    const winParts = [];
    if (win.h1 && win.h1.bytes_total) winParts.push(`${t('trafficWindow1h')}: ${win.h1.bytes_total_text}`);
    if (win.h24 && win.h24.bytes_total) winParts.push(`${t('trafficWindow24h')}: ${win.h24.bytes_total_text}`);
    if (win.h168 && win.h168.bytes_total) winParts.push(`${t('trafficWindow7d')}: ${win.h168.bytes_total_text}`);
    if (winParts.length) parts.push(winParts.join(' · '));
    if (data.average_batch) parts.push(`${t('trafficAvgBatch')}: ${data.average_batch_text}`);
    if (data.average_account) parts.push(`${t('trafficAvgAccount')}: ${data.average_account_text}`);
    summary.textContent = parts.join(' | ');
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
  const novproxyBtn = document.getElementById('novproxyExtractBtn');
  if (novproxyBtn) {
    novproxyBtn.onclick = async () => {
      if (dirty.size && !await saveConfig()) return;
      novproxyBtn.disabled = true;
      const originHtml = novproxyBtn.innerHTML;
      novproxyBtn.textContent = '提取中...';
      try {
        const r = await fetch('./api/proxy-pool/novproxy', {method: 'POST'});
        const d = await r.json();
        if (!r.ok) {
          setNotice(d.detail || 'NovProxy 提取失败', true);
        } else {
          setNotice(`NovProxy 成功提取 ${d.count} 个节点`);
          renderProxyStatus(d);
        }
      } catch (e) {
        setNotice(e.message, true);
      } finally {
        novproxyBtn.disabled = false;
        novproxyBtn.innerHTML = originHtml;
      }
    };
  }
  const qualityBtn = document.getElementById('qualityScanBtn');
  if (qualityBtn) {
    qualityBtn.onclick = async () => {
      qualityBtn.disabled = true;
      try {
        const r = await fetch('./api/quality/scan', {method: 'POST'});
        const d = await r.json();
        if (!r.ok) {
          setNotice(d.detail || '启动失败', true);
        } else {
          setNotice(d.message || '降智扫描已在后台启动');
          refreshCpaStatus();
        }
      } catch (e) {
        setNotice(e.message, true);
      } finally {
        setTimeout(() => { qualityBtn.disabled = false; }, 3000);
      }
    };
  }
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
