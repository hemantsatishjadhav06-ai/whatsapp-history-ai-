import {useEffect,useState} from 'react';
import {isOwnerAnswer,ownerAnswerExpiresAt} from '@milo/contracts';
import {currentPrivateResult,snapshotVersion,type AuthorizedSnapshot,type PrivateResult} from './private-results';

export function usePrivateResult<T>(snapshot:AuthorizedSnapshot|null):[T|null,(value:T|null,authorized?:AuthorizedSnapshot|null)=>void] {
  const [entry,setEntry]=useState<PrivateResult<T>|null>(null);
  const version=snapshotVersion(snapshot);
  useEffect(()=>{setEntry(previous=>previous&&currentPrivateResult(previous,snapshot)===null?null:previous);},[version,snapshot]);
  const expiry=entry&&isOwnerAnswer(entry.value)?ownerAnswerExpiresAt(entry.value):null;
  useEffect(()=>{if(expiry===null)return;const timeout=setTimeout(()=>setEntry(previous=>previous===entry?null:previous),Math.max(0,expiry-Date.now()));
    return()=>clearTimeout(timeout);},[expiry,entry]);
  return [currentPrivateResult(entry,snapshot),(value,authorized=snapshot)=>setEntry(value===null?null:{value,version:snapshotVersion(authorized)})];
}
