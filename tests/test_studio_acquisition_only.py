"""不匯出的流程仍須交付取得的素材；此測試只替代下載邊界。"""
from app.studio.contracts import WorkflowRequest
from app.studio.store import Store
from app.studio.worker import Worker


def test_video_acquisition_without_export_returns_video_assets(tmp_path):
    store=Store(tmp_path)
    project=store.create('project',{'name':'取得素材'})['id']
    source=store.create('source',{'kind':'youtube','url':'https://youtu.be/Yn2mE6_tMC8'},project)['id']
    plan=WorkflowRequest.model_validate({'source_id':source,'ranges':[{'start_us':6600000000,'end_us':6660000000}],
        'acquisition':{'video':'selected'},'analysis':{'mode':'none'},'subtitles':{'policy':'none'},
        'deliverables':{'formats':[]}})
    plan=store.create('plan',plan.model_dump(),project)
    job=store.submit(project,{'kind':'workflow','plan_id':plan['id']},'workflow')
    class Harness(Worker):
        def child(self,body,resource):
            assert body['kind'] in {'probe','acquire'}
            child=store.submit(project,body,resource)
            result={'duration_us':7200000000} if body['kind']=='probe' else {'asset_ids':['downloaded_video'],'saved_files':[{'asset_id':'downloaded_video'}]}
            store.finish(child['id'],'succeeded',result)
            return child['id']
    result=Harness(store,job).workflow()
    assert result['asset_ids']==['downloaded_video']
    assert result['saved_files']==[{'asset_id':'downloaded_video'}]
