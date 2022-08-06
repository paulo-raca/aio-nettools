from dataclasses import dataclass
import socket
import ctypes
from typing import Optional

class TcpInfo(ctypes.Structure):
    _fields_ = [
        ("tcpi_state", ctypes.c_uint8),
        ("tcpi_ca_state", ctypes.c_uint8),
        ("tcpi_retransmits", ctypes.c_uint8),
        ("tcpi_probes", ctypes.c_uint8),
        ("tcpi_backoff", ctypes.c_uint8),
        ("tcpi_options", ctypes.c_uint8),
        ("tcpi_snd_wscale", ctypes.c_uint8, 4),
        ("tcpi_rcv_wscale", ctypes.c_uint8, 4),
        ("tcpi_delivery_rate_app_limited", ctypes.c_uint8, 1),
        ("tcpi_fastopen_client_fail", ctypes.c_uint8, 2),

        ("tcpi_rto", ctypes.c_uint32),
        ("tcpi_ato", ctypes.c_uint32),
        ("tcpi_snd_mss", ctypes.c_uint32),
        ("tcpi_rcv_mss", ctypes.c_uint32),

        ("tcpi_unacked", ctypes.c_uint32),
        ("tcpi_sacked", ctypes.c_uint32),
        ("tcpi_lost", ctypes.c_uint32),
        ("tcpi_retrans", ctypes.c_uint32),
        ("tcpi_fackets", ctypes.c_uint32),

        # Times
        ("tcpi_last_data_sent", ctypes.c_uint32),
        ("tcpi_last_ack_sent", ctypes.c_uint32),  # Not remembered, sorry
        ("tcpi_last_data_recv", ctypes.c_uint32),
        ("tcpi_last_ack_recv", ctypes.c_uint32),

        # Metrics
        ("tcpi_pmtu", ctypes.c_uint32),
        ("tcpi_rcv_ssthresh", ctypes.c_uint32),
        ("tcpi_rtt", ctypes.c_uint32),
        ("tcpi_rttvar", ctypes.c_uint32),
        ("", ctypes.c_uint32),
        ("tcpi_snd_cwnd", ctypes.c_uint32),
        ("tcpi_advmss", ctypes.c_uint32),
        ("tcpi_reordering", ctypes.c_uint32),

        ("tcpi_rcv_rtt", ctypes.c_uint32),
        ("tcpi_rcv_space", ctypes.c_uint32),

        ("tcpi_total_retrans", ctypes.c_uint32),



	    ("tcpi_pacing_rate", ctypes.c_uint64),
        ("tcpi_max_pacing_rate", ctypes.c_uint64),
        ("tcpi_bytes_acked", ctypes.c_uint64),  # RFC4898 tcpEStatsAppHCThruOctetsAcked
        ("tcpi_bytes_received", ctypes.c_uint64),  # RFC4898 tcpEStatsAppHCThruOctetsReceived
        ("tcpi_segs_out", ctypes.c_uint32),  # RFC4898 tcpEStatsPerfSegsOut
        ("tcpi_segs_in", ctypes.c_uint32),  # RFC4898 tcpEStatsPerfSegsIn

	    ("tcpi_notsent_bytes", ctypes.c_uint32),
        ("tcpi_min_rtt", ctypes.c_uint32),
        ("tcpi_data_segs_in", ctypes.c_uint32),  # RFC4898 tcpEStatsDataSegsIn
        ("tcpi_data_segs_out", ctypes.c_uint32),  # RFC4898 tcpEStatsDataSegsOut

        ("tcpi_delivery_rate", ctypes.c_uint64),

        ("tcpi_busy_time", ctypes.c_uint64),  # Time (usec) busy sending data
        ("tcpi_rwnd_limited", ctypes.c_uint64),  # Time (usec) limited by receive window
        ("tcpi_sndbuf_limited", ctypes.c_uint64),  # Time (usec) limited by send buffer

        ("tcpi_delivered", ctypes.c_uint32),
        ("tcpi_delivered_ce", ctypes.c_uint32),

        ("tcpi_bytes_sent", ctypes.c_uint64),  # RFC4898 tcpEStatsPerfHCDataOctetsOut
        ("tcpi_bytes_retrans", ctypes.c_uint64),  # RFC4898 tcpEStatsPerfOctetsRetrans
        ("tcpi_dsack_dups", ctypes.c_uint32),  # RFC4898 tcpEStatsStackDSACKDups
        ("tcpi_reord_seen", ctypes.c_uint32),  # reordering events seen

        ("tcpi_rcv_ooopack", ctypes.c_uint32),  # Out-of-order packets received

        ("tcpi_snd_wnd", ctypes.c_uint32),  # peer's advertised receive window after scaling (bytes)
    ]

    def __repr__(self) -> str:
        values = []
        for field in self._fields_:
            field_name = field[0]
            field_value = getattr(self, field_name)
            values.append(f"{field_name}={field_value}")
        return f"TcpInfo({', '.join(values)})"

def get_tcpinfo(sock: Optional[socket.socket]) -> Optional[TcpInfo]:
    if sock is not None:
        try:
            data_blob = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, ctypes.sizeof(TcpInfo))
            return TcpInfo.from_buffer_copy(data_blob)
        except:
            pass
    return None
