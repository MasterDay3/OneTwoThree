import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"
import type { Meeting } from "@/types"

export const meetingsKey = ["meetings"] as const

export function useMeetings() {
  return useQuery({ queryKey: meetingsKey, queryFn: api.listMeetings })
}

export function useCreateMeeting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createMeeting,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meetingsKey }),
  })
}

export function useUpdateMeeting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.updateMeeting,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meetingsKey }),
  })
}

export function useSaveAgenda() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.saveAgenda,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: meetingsKey }),
  })
}

/** Removes the row immediately and restores it if the request fails. */
export function useDeleteMeeting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteMeeting,
    onMutate: async (id: string) => {
      await queryClient.cancelQueries({ queryKey: meetingsKey })
      const previous = queryClient.getQueryData<Meeting[]>(meetingsKey)
      queryClient.setQueryData<Meeting[]>(meetingsKey, (old) => old?.filter((m) => m.id !== id))
      return { previous }
    },
    onError: (_error, _id, context) => {
      if (context?.previous) queryClient.setQueryData(meetingsKey, context.previous)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: meetingsKey }),
  })
}
