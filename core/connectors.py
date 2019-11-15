import asyncio
import time
from typing import Any, Callable, Dict, List

import aiohttp

from core.transport.base import ServiceGatewayConnectorBase


class HTTPConnector:
    def __init__(self, session: aiohttp.ClientSession, url: str):
        self.session = session
        self.url = url

    async def send(self, payload: Dict, callback: Callable):
        service_send_time = time.time()
        try:
            async with self.session.post(self.url, json=payload['payload']) as resp:
                resp.raise_for_status()
                response = await resp.json()
                service_response_time = time.time()
                await callback(
                    task_id=payload['task_id'],
                    response=response[0],
                    service_send_time=service_send_time,
                    service_response_time=service_response_time
                )
        except Exception as e:
            response = e
            service_response_time = time.time()
            await callback(
                task_id=payload['task_id'],
                response=response,
                service_send_time=service_send_time,
                service_response_time=service_response_time
            )



class AioQueueConnector:
    def __init__(self, queue):
        self.queue = queue

    async def send(self, payload: Dict, **kwargs):
        await self.queue.put(payload)


class QueueListenerBatchifyer:
    def __init__(self, session, url, queue, batch_size):
        self.session = session
        self.url = url
        self.queue = queue
        self.batch_size = batch_size

    async def call_service(self, process_callable):
        while True:
            batch = []
            rest = self.queue.qsize()
            for _ in range(min(self.batch_size, rest)):
                item = await self.queue.get()
                batch.append(item)
            if batch:
                tasks = []
                model_payload = self.glue_tasks(batch)
                service_send_time = time.time()
                async with self.session.post(self.url, json=model_payload) as resp:
                    response = await resp.json()
                    service_response_time = time.time()
                for task, task_response in zip(batch, response):
                    tasks.append(
                        process_callable(
                            task_id=task['task_id'],
                            response=task_response,
                            service_send_time=service_send_time,
                            service_response_time=service_response_time))
                await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)

    def glue_tasks(self, batch):
        if len(batch) == 1:
            return batch[0]['payload']
        else:
            result = {k: [] for k in batch[0]['payload'].keys()}
            for el in batch:
                for k in result.keys():
                    result[k].extend(el['payload'][k])
            return result


class ConfidenceResponseSelectorConnector:
    async def send(self, payload: Dict, callback: Callable):
        service_send_time = time.time()
        response = payload['payload']['utterances'][-1]['hypotheses']
        best_skill = sorted(response, key=lambda x: x['confidence'], reverse=True)[0]
        response_time = time.time()
        await callback(
            task_id=payload['task_id'],
            response=best_skill,
            service_send_time=service_send_time,
            service_response_time=response_time)



class EventSetOutputConnector:
    def __init__(self, service_name: str):
        self.service_name = service_name

    async def send(self, payload, callback: Callable):
        event = payload['payload'].get('event', None)
        service_send_time = time.time()
        if not event or not isinstance(event, asyncio.Event):
            raise ValueError("'event' key is not presented in payload")
        event.set()
        service_response_time = time.time()
        await callback(task_id=payload['task_id'],
                       response=" ",
                       service_send_time=service_send_time,
                       service_response_time=service_response_time)


class AgentGatewayToChannelConnector:
    pass


class AgentGatewayToServiceConnector:
    _to_service_callback: Callable
    _service_name: str

    def __init__(self, to_service_callback: Callable, service_name: str):
        self._to_service_callback = to_service_callback
        self._service_name = service_name

    async def send(self, payload: Dict, **_kwargs):
        await self._to_service_callback(dialog=payload, service_name=self._service_name)


class ServiceGatewayHTTPConnector(ServiceGatewayConnectorBase):
    _session: aiohttp.ClientSession
    _url: str
    _service_name: str

    def __init__(self, service_config: dict, formatter: Callable) -> None:
        super().__init__(service_config, formatter)
        self._session = aiohttp.ClientSession()
        self._service_name = service_config['name']
        self._url = service_config['url']

    async def send_to_service(self, dialogs: List[Dict]) -> List[Any]:
        async with await self._session.post(self._url, json=self._formatter(dialogs)) as resp:
            responses_batch = await resp.json()

        return [{self._service_name: self._formatter(response, mode='out')} for response in responses_batch]
