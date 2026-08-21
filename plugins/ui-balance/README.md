# @deepseek-ai/dsh-client-ui-balance

> 鈿狅笍 **鏂扮増鏈畨瑁?娉ㄥ唽鏂瑰紡鏈夊彉鍖栵紝璇峰姟蹇呴槄璇绘湰鑺?*锛堝崌绾ц嚜 0.1.0-rc.4 鍙婃洿鏃╃増鏈椂锛夈€?
API 浣欓鑳跺泭锛氬彸涓嬭鎮诞鑳跺泭锛宯ode 鍗婇儴鏌ヨ鍚?provider 鐨勪綑棰濈鐐癸紙闆舵ā鍨?token锛夛紝鏀寔澶?API 鏉＄洰澧炲垹鏀广€?
## 瀹夎涓庢敞鍐岋紙鏂扮増鏈紝`dsh` 璧凤級

鏈彃浠舵槸 **dual-face 瀹㈡埛绔彃浠?*锛坄package.json` 閲屽彧鏈?`dsh.client`锛?*娌℃湁** `dsh.bundle`锛夈€傛柊鐗堟湰瀵规敞鍐屾柟寮忔敹绱э紝璇锋寜涓嬮潰鍋氾細

1. **涓嶈**鎶婂畠鏀捐繘 `dsh.profile.bundles` 鈥斺€?鏂扮増鏈細鐩存帴鎶ラ敊
   `profile bundle "@deepseek-ai/dsh-client-ui-balance" declares no dsh.bundle`銆?   瀹㈡埛绔彃浠?*鍙兘**閫氳繃 profile 鐨?`cordis.patch.yml` 鐢?`insert` 娉ㄥ唽鎴?`dsh.client` 琛岋細

   ```yaml
   # ~/.dsh/profiles/<name>/cordis.patch.yml
   - insert:
       - id: ui-balance
         name: '@deepseek-ai/dsh-client-ui-balance'
   ```

2. **Remote 绔偣蹇呴』鏈?`./typert` 瀹夸富娓呭崟**銆傚惁鍒?`/api/balance/*` 浼氳繑鍥?**HTTP 404**銆?   鏈寘宸插鍑?`./typert`锛坄lib/typert.host.js`锛夛紝鐢?`typert-loader` 鑷姩娉ㄥ唽杩涚綉鍏筹紱
   鏂板/淇敼 `@Remote` 鏂规硶鏃堕渶鍚屾鏇存柊璇ユ竻鍗曪紙鐢ㄧ敓鎴愬櫒 `typert.host.js` 閲嶇敓鎴愶紝
   鎴栦繚鎸佷笌 `src/client/remote.ts` 鐨?`TYPERT_REMOTE` 涓€鑷达級銆?
3. **profile 渚濊禆寤鸿鐢?`link:`锛坰ymlink锛夎€屼笉鏄?`file:`锛堟嫹璐濓級**锛?   `file:` 浼氬湪 `~/.dsh/profiles/<name>/node_modules` 閲岀暀涓€浠芥嫹璐濓紝婧愮爜鏀逛簡**涓嶄細鑷姩鍚屾**锛?   `link:` 鐩存帴鎸囧悜婧愮爜锛屾敼瀹屽嵆鏃剁敓鏁堛€?
```jsonc
// ~/.dsh/profiles/<name>/package.json
"dependencies": {
  "@deepseek-ai/dsh-client-ui-balance": "link:<your-dsh-path>/packages/client/ui-balance"
}
```

## 杩愯璇存槑

- node 鍗婇儴锛坄src/index.ts`锛夋敞鍐?`BalanceService`锛坄TypertRemoteService`锛夛紝鎻愪緵 `list/query/add/remove/update` 5 涓?Remote 绔偣銆?- 娴忚鍣ㄥ崐閮紙`src/client/`锛夐€氳繃 `ctx.remote.balance.*` 鍙栨暟锛岄浂妯″瀷 token锛屼笉杩涘叆浼氳瘽鏃ュ織銆?
## Model Experience

鏃?鈥斺€?鏈彃浠朵笉娉ㄥ唽浠讳綍妯″瀷鍙鐨?prompt/tool/浼氳瘽浜嬩欢锛涗綑棰濇暟鎹彧鍦?node 鍗婇儴鏌ヨ鍚庣粡 Remote 杈圭晫鍒拌揪娴忚鍣ㄣ€?