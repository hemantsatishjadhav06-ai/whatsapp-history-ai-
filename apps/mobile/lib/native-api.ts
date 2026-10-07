import {createClient,type ApiClient} from '@milo/contracts';

/** Bind every request to one captured native session; never use ambient cookies. */
export function nativeClient(origin:string,accessToken?:string,fetchImpl:typeof fetch=fetch):ApiClient {
  return createClient({baseUrl:origin,headers:accessToken?{Authorization:`Bearer ${accessToken}`}:{},
    fetchImpl:(input,init)=>fetchImpl(input,{...init,credentials:'omit'})});
}
